import discord

import os

import discord
import requests
import boto3
from discord.ext import commands, tasks
from discord.voice_client import VoiceClient
from discord.utils import get
from dotenv import load_dotenv

players = {}
queues = {}


def check_queue(track_id):
    if queues[track_id]:
        player = queues[track_id].pop(0)
        players[track_id] = player
        player.start()


if __name__ == '__main__':
    load_dotenv()
    polly_client = boto3.Session(
        aws_access_key_id=os.getenv('ACCESS_KEY'),
        aws_secret_access_key=os.getenv('SECRET_ACCESS_KEY'),
        region_name='eu-west-2').client('polly')

    TOKEN = os.getenv('DISCORD_TOKEN')

    bot = commands.Bot(command_prefix='/')

    counter = 0

    @bot.event
    async def on_ready():
        print(f'{bot.user.name} has connected to Discord!')

    @bot.command(name='btts', description="Makes the bot say your message in your voice channel",
                 help="Makes the bot say your message in your voice channel")
    # TODO make a tts queue
    async def to_tts(ctx):
        if ctx.author.voice.channel is None and not ctx.guild.voice_client:
            await ctx.send("Your are not currently connected to a voice channel")
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
        vc.play(discord.FFmpegPCMAudio(source='speech.mp3'))

    # @bot.command(name='play_song', help='To play song')
    # async def play(ctx, url):
    #     try:
    #         server = ctx.message.guild
    #         voice_channel = server.voice_client
    #
    #         async with ctx.typing():
    #             r = requests.get(url)
    #     except:
    #         await ctx.send("The bot is not connected to a voice channel.")

    @bot.command(name='stop', description="Stops the current Voice message", help="Stops the current Voice message")
    async def stop_tts(ctx):
        if ctx.guild.voice_client:
            await ctx.guild.voice_client.stop()

    @bot.command(name='leave', description="Makes the bot leave the current voice channel",
                 help="Makes the bot leave the current voice channel")
    async def stop_tts(ctx):
        voice_client = get(ctx.bot.voice_clients, guild=ctx.guild)
        if voice_client and voice_client.is_connected():
            await voice_client.disconnect()


    bot.run(TOKEN)
