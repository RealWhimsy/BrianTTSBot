import os
import nextcord
import requests
import boto3
import asyncio
import uuid
import glob
import threading
from nextcord import Forbidden, channel
from nextcord.ext import commands, tasks
from nextcord.utils import get
from nextcord.ext.commands import CommandNotFound
from dotenv import load_dotenv
import voices

AUTO_TIMEOUT_SECONDS = 300  # bot will automatically leave after not sending voice activity for this many seconds
already_playing = {}  # dictionary that maps guild id to playing state: true if the bot is already playing
last_play = {}  # unique id of the last played track, used to check for idle times
has_played_once = {}  # true if the bot has played tts at least once after joining a voice channel
guild_id_to_filenames = {}  # maps guild id to audio filenames
guild_id_to_voice_id = {}

load_dotenv()
polly_client = boto3.Session(
    aws_access_key_id=os.getenv('ACCESS_KEY'),
    aws_secret_access_key=os.getenv('SECRET_ACCESS_KEY'),
    region_name='eu-west-2').client('polly')

TOKEN = os.getenv('DISCORD_TOKEN')

client = commands.Bot(command_prefix='$')


def play(vc):
    """
    Play a voice clip.
    Calls itself recursively until all added clips have been played.
    :param vc: the Discord VoiceClient in use
    """
    global already_playing, guild_id_to_filenames

    guild_id = vc.guild.id
    already_playing[guild_id] = True

    if len(guild_id_to_filenames[guild_id]) <= 0:
        already_playing[guild_id] = False
        return

    filename = guild_id_to_filenames[guild_id][0]

    if vc.is_connected():
        if os.path.isfile("./" + filename):
            vc.play(nextcord.FFmpegPCMAudio(source=filename), after=lambda e: clean_up_after_play(vc))
        else:
            try:
                guild_id_to_filenames[guild_id].remove(filename)
            except Exception:
                pass  # don't judge me, this is scuffed af


def delete_file_from_guild_id(guild_id, filename, number_try):
    if number_try > 3:
        print("Failed to delete file after " + str(number_try) + " tries! Cleanup...")
        delete_dead_files()
        return

    try:
        os.remove(os.path.join('./', filename))
    except Exception:
        timer = threading.Timer(2.0, lambda: delete_file_from_guild_id(guild_id, filename, number_try + 1))
        timer.start()


def clean_up_after_play(vc):
    guild_id = vc.guild.id
    if len(guild_id_to_filenames[guild_id]) > 0:
        del_filename = guild_id_to_filenames[guild_id].pop(0)
        delete_file_from_guild_id(guild_id, del_filename, 1)

    play(vc)


async def auto_leave(vc):
    global has_played_once

    await asyncio.sleep(AUTO_TIMEOUT_SECONDS)

    if vc.guild.id not in has_played_once or not has_played_once[vc.guild.id]:
        await disconnect(vc)


async def disconnect(vc):
    global has_played_once
    if vc and vc.is_connected():
        await vc.disconnect()
        has_played_once[vc.guild.id] = False


@client.event
async def on_ready():
    print(f'{client.user.name} has connected to Discord!')
    await client.change_presence(activity=nextcord.Game(name="Type '/btts <message>' for TTS!"))


@client.event
async def on_voice_state_update(member, before, after):
    if after.channel is None and before.channel is not None:
        if member.id == client.application_id:
            guild_id = before.channel.guild.id

            has_played_once[guild_id] = False
            already_playing[guild_id] = False

            if member.guild.voice_client is not None:
                if guild_id in guild_id_to_filenames:
                    member.guild.voice_client.stop()
                await member.guild.voice_client.disconnect()

            guild_id_to_filenames[guild_id].clear()
            delete_dead_files()



@client.event
async def on_command_error(ctx, error):
    """
    Ignore errors that occur by calling commands that do not exist (e.g. because of typos or other bots with the
    same prefix).
    """
    if isinstance(error, CommandNotFound):
        return
    raise error


def delete_dead_files():
    mp3_files = glob.iglob('./*.mp3', recursive=False)

    files_to_keep = []

    for file_list in guild_id_to_filenames.items():
        for file in file_list:
            files_to_keep.append(file)

    for file in mp3_files:
        if file not in files_to_keep:
            try:
                os.remove(os.path.join('./', file))
            except PermissionError:
                # file currently in use - ignore for now
                pass


class ChannelCommands(commands.Cog):
    """
    Contains all commands regarding channel movement
    """

    @client.slash_command(name='leave', description="Makes the bot leave the current voice channel")
    async def leave(self, ctx):
        global has_played_once, guild_id_to_filenames
        await ctx.response.send_message("Disconnected!")
        guild_id = ctx.guild.id

        if guild_id in guild_id_to_filenames:
            while len(guild_id_to_filenames[guild_id]) > 0:
                if ctx.guild.voice_client:
                    ctx.guild.voice_client.stop()

        # disconnect
        voice_client = get(ctx.client.voice_clients, guild=ctx.guild)
        has_played_once[ctx.guild.id] = False
        await disconnect(voice_client)
        del voice_client

        # clean up
        delete_dead_files()


    @client.slash_command(name='move', description="Moves the bot to your current voice channel")
    async def move(self, ctx):
        if ctx.user.voice is None:
            await ctx.response.send_message("Your are not currently connected to a voice channel!")
            return

        ch = ctx.user.voice.channel
        if ctx.guild.voice_client is None:
            await ch.connect()
            await ctx.response.send_message("Hello there!")
            return

        await ctx.guild.voice_client.move_to(ch)
        await ctx.response.send_message("Moved!")

    @client.slash_command(name='join', description="Makes the bot join a specified voice channel.")
    async def join(self, ctx, channel_name):
        voice_channels = []  # holds all voice channels
        channels = ctx.guild.channels  # all channels (including text from) on the server
        for ch in channels:
            if isinstance(ch, channel.VoiceChannel):
                voice_channels.append(ch)  # if a voice channel is found, add it to the list

        # ignore capitalization and remove all non-ascii symbols (such as emojis)
        ch_name = str(channel_name).lower().encode('ascii', 'ignore').decode('ascii').strip()

        # search all voice channels for one that matches the passed name
        for ch in voice_channels:
            normalized_ch = str(ch).lower().encode('ascii', 'ignore').decode('ascii').strip()
            if normalized_ch == ch_name:
                # channel was found, check if already in voice
                if not ctx.guild.voice_client:
                    await ch.connect()
                    ctx.guild.voice_client.stop()
                    await ctx.response.send_message("Hello there!")
                else:
                    await ctx.guild.voice_client.move_to(ch)
                    await ctx.response.send_message("Hello there!")
                await auto_leave(ctx.guild.voice_client)
                return

        await ctx.response.send_message("Could not find the specified voice channel :frowning:")


class PlayCommands(commands.Cog):
    """
    Contains all commands regarding playback
    """

    @client.slash_command(name='btts',
                          description="Makes the bot say your message in your voice channel.")
    async def to_tts(self, ctx, message:str):
        global already_playing, last_play, has_played_once, guild_id_to_filenames
        if not hasattr(ctx.user, 'voice'):
            return
        if ctx.user.voice is None and not ctx.guild.voice_client:
            await ctx.send("Your are not currently connected to a voice channel!")
            return

        if not ctx.guild.voice_client:
            ch = ctx.user.voice.channel
            vc = await ch.connect()
        else:
            vc = ctx.guild.voice_client

        # only allow up to 1000 characters per message
        if len(message) > 1000:
            message = message[:1000]

        await ctx.response.send_message(f"{ctx.user.display_name} said `{message}`")

        if ctx.guild.id not in guild_id_to_voice_id:
            guild_id_to_voice_id[ctx.guild.id] = voices.BRIAN["name"]

        response = polly_client.synthesize_speech(VoiceId=guild_id_to_voice_id[ctx.guild.id],
                                                  OutputFormat='mp3',
                                                  Text=message,
                                                  Engine='standard')

        speech_id = uuid.uuid4()
        filename = 'speech_' + speech_id.hex + '.mp3'
        file = open(filename, 'wb')
        file.write(response['AudioStream'].read())
        file.close()
        if ctx.guild.id not in guild_id_to_filenames:
            guild_id_to_filenames[ctx.guild.id] = []

        if ctx.guild.id not in already_playing:
            already_playing[ctx.guild.id] = False

        guild_id_to_filenames[ctx.guild.id].append(filename)
        if not already_playing[ctx.guild.id]:
            play(vc)

        obj = object()
        last_play[ctx.guild.id] = id(obj)
        has_played_once[ctx.guild.id] = True

        await asyncio.sleep(AUTO_TIMEOUT_SECONDS)

        if last_play[ctx.guild.id] == id(obj):
            await disconnect(vc)

    @client.slash_command(name='skip', description="Skips the current Voice message")
    async def skip_tts(self, ctx):
        if ctx.guild.voice_client:
            ctx.guild.voice_client.stop()

        await ctx.response.send_message("Skipped!")


class Info(commands.Cog):
    """
    Contains informative commands
    """

    @client.slash_command(name='manual', description="Shows a link to a helpful TTS documentation")
    async def manual(self, ctx):
        await ctx.response.send_message("Brian TTS for Pepegas:\n "
                                        "https://docs.google.com/document/d/1qLKdc3QArtn6PVuGf42EfoMuzvLE_ykWwU1RViEcrbU/edit")

    @client.slash_command(name='support', description="Support the bot by voting/reviewing it on top.gg")
    async def support(self, ctx):
        await ctx.response.send_message(
            "Support the bot by voting/reviewing it on top.gg. Every vote is appreciated! \n"
            "https://top.gg/bot/860190148179394591")


class VoiceCommands(commands.Cog):
    """
    Contains commands for setting the voice of the bot
    """

    @client.slash_command(name='voices1', description="Shows the first table of available voices.")
    async def voices_1(self, ctx):
        lines = self.build_voice_table(voices.voices_1)

        await ctx.response.send_message(lines)

    @client.slash_command(name='voices2', description="Shows the second table of available voices.")
    async def voices_2(self, ctx):
        lines = self.build_voice_table(voices.voices_2)

        await ctx.response.send_message(lines)

    @client.slash_command(name='voices3', description="Shows the third table of available voices")
    async def voices_3(self, ctx):
        lines = self.build_voice_table(voices.voices_3)

        await ctx.response.send_message(lines)

    @client.slash_command(name='setvoice', description="Sets the voice of the bot.")
    async def set_voice(self, ctx, voice: str):
        name = voice
        name = name.replace('>', '')  # remove '>' in case the user entered the name like "<name>"
        name = name.replace('<', '')  # remove '<' in case the user entered the name like "<name>"
        name = name.capitalize()  # Capitalize the name

        is_available_voice = False
        for voice in voices.all_voices:
            if voice["name"].lower() == name.lower().strip():
                is_available_voice = True
                break

        if not is_available_voice:
            await ctx.response.send_message("Sorry, " + name + " is not an available voice!\nUse ```/voices1```, ```/voices2``` or ```/voices3``` to see a list of all "
                                              "available voices, then use ```/setvoice <name>``` to set the voice.")

        else:
            guild_id_to_voice_id[ctx.guild.id] = name
            await ctx.response.send_message("TTS voice successfully set to " + name + "!")

    @client.slash_command(name='currentvoice', description="Shows information about the currently set voice.")
    async def current_voice(self, ctx):

        if ctx.guild.id not in guild_id_to_voice_id:
            guild_id_to_voice_id[ctx.guild.id] = "Brian"

        voice_name = guild_id_to_voice_id[ctx.guild.id]
        voice_dict = None

        for voice in voices.all_voices:
            if voice["name"] == voice_name:
                voice_dict = voice
                break

        if voice_dict is not None:
            await ctx.response.send_message("Current voice info:\n" + "```" +
                           "Language: " + voice_dict["language"] + "\n" +
                           "Name    : " + voice_dict["name"] + "\n" +
                           "Gender  : " + voice_dict["gender"]
                           + "```"
                           )

    def build_voice_table(self, voice_array):
        num_language_separators = 31
        num_name_separators = 18
        num_gender_separators = 19

        lines = "```\n+-------------------------------+------------------+-------------------+\n"
        lines += "+           Language            +       Name       +       Gender      +\n"
        lines += "+-------------------------------+------------------+-------------------+\n"

        for voice in voice_array:
            language_length = len(voice["language"])
            name_length = len(voice["name"])
            gender_length = len(voice["gender"])

            lines += "| " + voice["language"]
            for x in range(1, num_language_separators - language_length):
                lines += " "

            lines += "| " + voice["name"]
            for x in range(1, num_name_separators - name_length):
                lines += " "

            lines += "| " + voice["gender"]
            for x in range(1, num_gender_separators - gender_length):
                lines += " "

            lines += "|\n"

        lines += "+-------------------------------+------------------+-------------------+```"

        return lines


class AdminCommands(commands.Cog):
    """
    These commands are only available in the bot's dev server
    """

    @client.slash_command(name='broadcast', description="Sends a message to all servers the bot is in",
                          guild_ids=[214106612552433665])
    async def broadcast(self, ctx, message):
        await ctx.response.send_message("Message sent!")
        for server in client.guilds:
            for ch in server.text_channels:
                try:
                    await ch.send(message)
                except Exception:
                    # yep, that's how we do it
                    continue
                else:
                    break

    @client.slash_command(name='broadcastpreview', description="Preview how the broadcast message would look like",
                          guild_ids=[214106612552433665])
    async def broadcast_preview(self, ctx, message):
        await ctx.response.send_message(message)

    @client.slash_command(name='servercount', description="Shows how many servers the bot is in!",
                          guild_ids=[214106612552433665])
    async def server_count(self, ctx):
        await ctx.response.send_message("I'm in " + str(len(client.guilds)) + " servers!")


client.remove_command('help')  # remove default help command to make room for the custom one
client.add_cog(PlayCommands())
client.add_cog(ChannelCommands())
client.add_cog(VoiceCommands())
client.add_cog(Info())
client.add_cog(AdminCommands())

client.run(TOKEN)
