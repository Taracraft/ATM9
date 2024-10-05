import asyncio
import datetime
import json
import discord
import logging.handlers
import requests
from discord.ext.tasks import loop
from discord.ui import Button, View

# Intents
intents = discord.Intents.all()
intents.members = True
intents.messages = True
intents.presences = True
client = discord.Client(intents=intents)

# Logging
logger = logging.getLogger('discord')
logger.setLevel(logging.DEBUG)
logging.getLogger('discord.http').setLevel(logging.INFO)
handler = logging.handlers.RotatingFileHandler(
    filename='Twitch.log',
    encoding='utf-8',
    maxBytes=32 * 1024 * 1024,  # 32 MiB
    backupCount=5,  # Rotate durch 5 files
)
dt_fmt = '%Y-%m-%d %H:%M:%S'
formatter = logging.Formatter('[{asctime}] [{levelname:<8}] {name}: {message}', dt_fmt, style='{')
handler.setFormatter(formatter)
logger.addHandler(handler)

# Datei öffnen und Inhalt laden
with open("/home/discord/ATM9/Twitch/config.json") as config_file:
    config = json.load(config_file)

# Token aus der Konfigurationsdatei abrufen
discord_token = config.get("discord_token")


def get_app_access_token():
    params = {
        "client_id": config["client_id"],
        "client_secret": config["client_secret"],
        "grant_type": "client_credentials"
    }
    try:
        response = requests.post("https://id.twitch.tv/oauth2/token", params=params)
        logger.debug(f"Token-Antwort: {response.text}")
        response.raise_for_status()  # Check if the request was successful
        response_data = response.json()
        logger.debug(f"Antwort Daten: {response_data}")
        access_token = response_data.get("access_token")
        if not access_token:
            logger.error("Access-Token konnte nicht aus der Antwort gelesen werden.")
            return None
        return access_token
    except requests.exceptions.RequestException as e:
        logger.error(f"Fehler bei der Anforderung des Access-Tokens: {e}")
        return None


def should_update_token():
    try:
        with open("/home/discord/ATM9/Twitch/last_token_update.txt", "r") as f:
            last_update_str = f.read().strip()
            last_update = datetime.datetime.strptime(last_update_str, "%Y-%m-%d").replace(tzinfo=datetime.timezone.utc)
    except FileNotFoundError:
        return True
    next_update = last_update + datetime.timedelta(days=60)
    return datetime.datetime.now(datetime.timezone.utc) >= next_update


def update_token_timestamp():
    with open("/home/discord/ATM9/Twitch/last_token_update.txt", "w") as f:
        f.write(datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d"))


access_token = None


def ensure_token():
    global access_token
    if not access_token or should_update_token():
        access_token = get_app_access_token()
        update_token_timestamp()
    return access_token


def get_users(login_names):
    ensure_token()
    params = {
        "login": login_names
    }
    headers = {
        "Authorization": "Bearer {}".format(access_token),
        "Client-Id": config["client_id"]
    }
    response = requests.get("https://api.twitch.tv/helix/users", params=params, headers=headers)
    if response.status_code == 401:
        logger.error("Unauthorized access - invalid OAuth token.")
        ensure_token()  # Refresh token and try again
        headers["Authorization"] = "Bearer {}".format(access_token)
        response = requests.get("https://api.twitch.tv/helix/users", params=params, headers=headers)

    logger.debug(f"Twitch-Api Antwort: {response.text}")
    response.raise_for_status()  # Raises an HTTPError if the HTTP request returned an unsuccessful status code

    # Jetzt geben wir die User-ID und die Profilbild-URL zurück
    users = {entry["login"]: {"id": entry["id"], "profile_image_url": entry["profile_image_url"]} for entry in response.json()["data"]}
    logger.debug(f"Users: {users}")
    return users


def get_streams(users):
    ensure_token()
    params = {
        "user_id": [user_data["id"] for user_data in users.values()]  # IDs für die Abfrage
    }
    headers = {
        "Authorization": "Bearer {}".format(access_token),
        "Client-Id": config["client_id"]
    }
    response = requests.get("https://api.twitch.tv/helix/streams", params=params, headers=headers)
    if response.status_code == 401:
        logger.error("Unauthorized access - invalid OAuth token.")
        ensure_token()  # Refresh token and try again
        headers["Authorization"] = "Bearer {}".format(access_token)
        response = requests.get("https://api.twitch.tv/helix/streams", params=params, headers=headers)

    logger.debug(f"Twitch-Api Antwort: {response.text}")
    response.raise_for_status()  # Check if the request was successful

    return {entry["user_login"]: entry for entry in response.json()["data"]}


online_users = {}


def get_notifications():
    users = get_users(config["watchlist"])
    streams = get_streams(users)

    logger.info("Users retrieved: %s", users)
    logger.info("Streams retrieved: %s", streams)
    notifications = []

    for user_name in config["watchlist"]:
        user_name_cleaned = user_name.strip().lower()
        if user_name_cleaned in (name.strip().lower() for name in streams.keys()):
            logger.info("User %s is streaming: %s", user_name, streams[user_name_cleaned])
            notifications.append(streams[user_name_cleaned])
            logger.info("Added to notifications: %s", streams[user_name_cleaned])
        else:
            logger.info("User %s is not currently streaming", user_name)

    logger.info("Final Notifications: %s", notifications)
    return notifications


@loop(seconds=60)
async def check_twitch_online_streamers():
    channel = client.get_channel(1291808950882144310)
    logger.info("Channel: %s", channel)
    users = get_users(config["watchlist"])
    streams = get_streams(users)
    logger.info("Streams retrieved: %s", streams)

    for streamer_name, user_data in users.items():
        stream_info = streams.get(streamer_name.lower())
        if stream_info:
            streamer_name = stream_info["user_login"]
            stream_url = f"https://www.twitch.tv/{streamer_name}"

            embed = discord.Embed(
                title=f"{streamer_name} ist jetzt online!",
                description=f"Klicke auf den Button, um den Stream zu sehen!",
                color=discord.Color.green()
            )

            # Setze das Profilbild des Streamers als Thumbnail
            embed.set_thumbnail(url=user_data["profile_image_url"])

            # Button erstellen
            button = Button(label="Stream anschauen", url=stream_url)

            # View für den Button erstellen
            view = View()
            view.add_item(button)

            # Nachricht mit Embed und Button senden
            await channel.send(embed=embed, view=view)

async def main():
    @client.event
    async def on_ready():
        print("Bot is ready!")
        print("Logged in as: " + client.user.name)
        print("Bot ID: " + str(client.user.id))
        for guild in client.guilds:
            print("Connected to server: {}".format(guild))
        print("------")
        print("Starting up...")
        channels = client.get_channel(1291808950882144310)
        print('Clearing messages...')
        await channels.purge(limit=1000)
        # Start the loop task
        await check_twitch_online_streamers()


if __name__ == '__main__':
    asyncio.run(main())
    client.run(discord_token)
