import os
import discord
import requests
import boto3
from discord.ext import commands, tasks
from discord.voice_client import VoiceClient
from discord.utils import get
from dotenv import load_dotenv

counter = 0
played_counter = 0
already_playing = False

load_dotenv()
polly_client = boto3.Session(
    aws_access_key_id=os.getenv('ACCESS_KEY'),
    aws_secret_access_key=os.getenv('SECRET_ACCESS_KEY'),
    region_name='eu-west-2').client('polly')

TOKEN = os.getenv('DISCORD_TOKEN')

bot = commands.Bot(command_prefix='/')


def play(vc, is_incrementing=False):
    """
    Play a voice clip.
    Calls itself recursively until all added clips have been played.
    :param vc: the Discord VoiceClient in use
    :param is_incrementing: whether the played_counter should be incremented or not
    """
    global played_counter, counter, already_playing

    if not already_playing:
        already_playing = True

    if is_incrementing:
        played_counter += 1

    # delete all files and reset the queue when all files have been played
    if played_counter == counter:
        clear_queue()
        return

    vc.play(discord.FFmpegPCMAudio(source='speech' + str(played_counter) + '.mp3'), after=lambda e: play(vc, True))


def clear_queue():
    global counter, played_counter, already_playing
    folder = './'
    filelist = [f for f in os.listdir(folder) if f.endswith(".mp3")]
    for file in filelist:
        os.remove(os.path.join(folder, file))

    counter = 0
    played_counter = 0
    already_playing = False


@bot.event
async def on_ready():
    print(f'{bot.user.name} has connected to Discord!')


@bot.command(name='bible', help="Shows a link to a helpful TTS documentation", category="Help")
async def bible(ctx):
    await ctx.send("Brian TTS for Pepegas:\n "
                   "https://docs.google.com/document/d/1qLKdc3QArtn6PVuGf42EfoMuzvLE_ykWwU1RViEcrbU/edit")


class ChannelCommands(commands.Cog):
    """
    Contains all commands regarding channel movement
    """

    @commands.command(name='leave', description="Makes the bot leave the current voice channel",
                      help="Makes the bot leave the current voice channel")
    async def leave(self, ctx):
        voice_client = get(ctx.bot.voice_clients, guild=ctx.guild)
        if voice_client and voice_client.is_connected():
            await voice_client.disconnect()

    @commands.command(name='move', description="Moves the bot to your current voice channel",
                      help="Moves the bot to your current voice channel")
    async def move(self, ctx):
        if ctx.author.voice is None:
            await ctx.send("Your are not currently connected to a voice channel!")
            return

        channel = ctx.author.voice.channel
        await ctx.guild.voice_client.move_to(channel)


class PlayCommands(commands.Cog):
    """
    Contains all commands regarding playback
    """

    @commands.command(name='btts',
                      description="Makes the bot say your message in your voice channel. Type '/btts <your message"
                                  "here> to trigger voice playback",
                      help="Makes the bot say your message in your voice channel")
    async def to_tts(self, ctx):
        global counter, already_playing
        if ctx.author.voice is None and not ctx.guild.voice_client:
            await ctx.send("Your are not currently connected to a voice channel!")
            return

        if not ctx.guild.voice_client:
            channel = ctx.author.voice.channel
            vc = await channel.connect()
        else:
            vc = ctx.guild.voice_client

        text = ctx.message.content[6:]  # remove "/btts " prefix from the actual text

        response = polly_client.synthesize_speech(VoiceId='Brian',
                                                  OutputFormat='mp3',
                                                  Text=text,
                                                  Engine='standard')

        file = open('speech' + str(counter) + '.mp3', 'wb')
        file.write(response['AudioStream'].read())
        file.close()
        counter += 1
        if not already_playing:
            play(vc)

    @commands.command(name='skip', description="Skips the current Voice message",
                      help="Skips the current Voice message")
    async def skip_tts(self, ctx):
        if ctx.guild.voice_client:
            ctx.guild.voice_client.stop()

    @commands.command(name='stop', description="Fully stops playback and deletes the queue",
                      help="Fully stops playback and deletes the queue")
    async def stop_tts(self, ctx):
        global counter, played_counter
        while played_counter < counter:
            ctx.guild.voice_client.stop()


bot.add_cog(PlayCommands())
bot.add_cog(ChannelCommands())

bot.run(TOKEN)
