import os
import discord
import requests
import boto3
from discord import Forbidden
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

bot = commands.Bot(command_prefix='&')


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


async def send_embed(ctx, embed):
    """
    Function that handles the sending of embeds
    -> Takes context and embed to send
    - tries to send embed in channel
    - tries to send normal message when that fails
    - tries to send embed private with information abot missing permissions
    If this all fails: https://youtu.be/dQw4w9WgXcQ
    """
    try:
        await ctx.send(embed=embed)
    except Forbidden:
        try:
            await ctx.send("Hey, seems like I can't send embeds. Please check my permissions :)")
        except Forbidden:
            await ctx.author.send(
                f"Hey, seems like I can't send any message in {ctx.channel.name} on {ctx.guild.name}\n"
                f"May you inform the server team about this issue? :slight_smile: ", embed=embed)


@bot.event
async def on_ready():
    print(f'{bot.user.name} has connected to Discord!')


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


class Info(commands.Cog):
    """
    Contains informative commands
    """

    @commands.command(name='bible', help="Shows a link to a helpful TTS documentation")
    async def bible(self, ctx):
        await ctx.send("Brian TTS for Pepegas:\n "
                       "https://docs.google.com/document/d/1qLKdc3QArtn6PVuGf42EfoMuzvLE_ykWwU1RViEcrbU/edit")


# advanced help message by Chris#0001 https://gist.github.com/nonchris/1c7060a14a9d94e7929aa2ef14c41bc2
class Bttshelp(commands.Cog):
    """
    Sends this help message
    """

    def __init__(self, bot):
        self.bot = bot

    @commands.command()
    # @commands.bot_has_permissions(add_reactions=True,embed_links=True)
    async def bttshelp(self, ctx, *input):
        """Shows all modules of that bot"""

        # !SET THOSE VARIABLES TO MAKE THE COG FUNCTIONAL!
        prefix = '&'
        version = 1.0

        # setting owner name - if you don't wanna be mentioned remove line 49-60 and adjust help text (line 88)
        owner = "Whimsy#5457"
        owner_name = "Whimsy"

        # checks if cog parameter was given
        # if not: sending all modules and commands not associated with a cog
        if not input:
            # checks if owner is on this server - used to 'tag' owner
            try:
                owner = ctx.guild.get_member(owner).mention

            except AttributeError as e:
                owner = owner

            # starting to build embed
            emb = discord.Embed(title='Commands and modules', color=discord.Color.blue(),
                                description=f'Use `{prefix}bttshelp <module>` to gain more information about that module '
                                            f':smiley:\n')

            # iterating trough cogs, gathering descriptions
            cogs_desc = ''
            for cog in self.bot.cogs:
                cogs_desc += f'`{cog}` {self.bot.cogs[cog].__doc__}\n'

            # adding 'list' of cogs to embed
            emb.add_field(name='Modules', value=cogs_desc, inline=False)

            # integrating trough uncategorized commands
            commands_desc = ''
            for command in self.bot.walk_commands():
                # if cog not in a cog
                # listing command if cog name is None and command isn't hidden
                if not command.cog_name and not command.hidden:
                    commands_desc += f'{command.name} - {command.help}\n'

            # adding those commands to embed
            if commands_desc:
                emb.add_field(name='Not belonging to a module', value=commands_desc, inline=False)

            # setting information about author
            emb.add_field(name="About", value=f"BrianBot is developed by Whimsy#5457, based on discord.py.\n\
                                    Please visit https://github.com/RealWhimsy/BrianTTSBot to submit ideas or bugs.")
            emb.set_footer(text=f"Bot is running {version}")

        # block called when one cog-name is given
        # trying to find matching cog and it's commands
        elif len(input) == 1:

            # iterating trough cogs
            for cog in self.bot.cogs:
                # check if cog is the matching one
                if cog.lower() == input[0].lower():

                    # making title - getting description from doc-string below class
                    emb = discord.Embed(title=f'{cog} - Commands', description=self.bot.cogs[cog].__doc__,
                                        color=discord.Color.green())

                    # getting commands from cog
                    for command in self.bot.get_cog(cog).get_commands():
                        # if cog is not hidden
                        if not command.hidden:
                            emb.add_field(name=f"`{prefix}{command.name}`", value=command.help, inline=False)
                    # found cog - breaking loop
                    break

            # if input not found
            # yes, for-loops have an else statement, it's called when no 'break' was issued
            else:
                emb = discord.Embed(title="What's that?!",
                                    description=f"I've never heard from a module called `{input[0]}` before :scream:",
                                    color=discord.Color.orange())

        # too many cogs requested - only one at a time allowed
        elif len(input) > 1:
            emb = discord.Embed(title="That's too much.",
                                description="Please request only one module at once :sweat_smile:",
                                color=discord.Color.orange())

        else:
            emb = discord.Embed(title="It's a magical place.",
                                description="I don't know how you got here. But I didn't see this coming at all.\n"
                                            "Would you please be so kind to report that issue to me on github?\n"
                                            "https://github.com/RealWhimsy/BrianTTSBot/issues",
                                color=discord.Color.red())

        # sending reply embed using our own function defined above
        await send_embed(ctx, emb)


bot.remove_command('help')
bot.add_cog(PlayCommands())
bot.add_cog(ChannelCommands())
bot.add_cog(Info())
bot.add_cog(Bttshelp(bot))

bot.run(TOKEN)
