import os
import discord
import requests
import boto3
import asyncio
import uuid
from discord import Forbidden, client, channel
from discord.ext import commands, tasks
from discord.voice_client import VoiceClient
from discord.utils import get
from discord.ext.commands import CommandNotFound
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

bot = commands.Bot(command_prefix='$')


def play(vc, is_incrementing=False):
    """
    Play a voice clip.
    Calls itself recursively until all added clips have been played.
    :param vc: the Discord VoiceClient in use
    :param is_incrementing: true when the call was made from the after-function
    """
    global already_playing, guild_id_to_filenames

    guild_id = vc.guild.id
    if not already_playing[guild_id] and vc.is_connected():
        if len(guild_id_to_filenames[guild_id]) > 1:
            for x in range(0, len(guild_id_to_filenames[guild_id]) - 1):
                del_filename = guild_id_to_filenames[guild_id].pop(0)
                os.remove(os.path.join('./', del_filename))

        already_playing[guild_id] = True

    if len(guild_id_to_filenames[guild_id]) <= 0:
        already_playing[guild_id] = False
        return

    if is_incrementing:
        del_filename = guild_id_to_filenames[guild_id].pop(0)
        os.remove(os.path.join('./', del_filename))

    if len(guild_id_to_filenames[guild_id]) <= 0:
        already_playing[guild_id] = False
        return

    filename = guild_id_to_filenames[guild_id][0]

    if vc.is_connected():
        if os.path.isfile("./" + filename):
            vc.play(discord.FFmpegPCMAudio(source=filename), after=lambda e: play(vc, True))
        else:
            try:
                guild_id_to_filenames[guild_id].remove(filename)
            except Exception:
                pass  # don't judge me, this is scuffed af


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


@bot.event
async def on_voice_state_update(member, before, after):
    if after.channel is None and before.channel is not None:
        guild_id = before.channel.guild.id

        has_played_once[guild_id] = False
        already_playing[guild_id] = False

        if member.guild.voice_client is not None:
            await member.guild.voice_client.disconnect()


@bot.event
async def on_command_error(ctx, error):
    """
    Ignore errors that occur by calling commands that do not exist (e.g. because of typos or other bots with the
    same prefix).
    """
    if isinstance(error, CommandNotFound):
        return
    raise error


class ChannelCommands(commands.Cog):
    """
    Contains all commands regarding channel movement
    """

    @commands.command(name='leave', description="Makes the bot leave the current voice channel",
                      help="Makes the bot leave the current voice channel")
    async def leave(self, ctx):
        global has_played_once, guild_id_to_filenames
        # stop playback first
        guild_id = ctx.guild.id
        while len(guild_id_to_filenames[guild_id]) > 0:
            ctx.guild.voice_client.stop()

        # then disconnect
        voice_client = get(ctx.bot.voice_clients, guild=ctx.guild)
        has_played_once[ctx.guild.id] = False
        await disconnect(voice_client)

    @commands.command(name='move', description="Moves the bot to your current voice channel",
                      help="Moves the bot to your current voice channel")
    async def move(self, ctx):
        if ctx.author.voice is None:
            await ctx.send("Your are not currently connected to a voice channel!")
            return

        ch = ctx.author.voice.channel
        await ctx.guild.voice_client.move_to(ch)

    @commands.command(name='join',
                      description="Makes the bot join a specified voice channel. Type '$join <channel_name>' to make"
                                  "the bot join.",
                      help='Makes the bot join a specified voice channel')
    async def join(self, ctx, ch_name=None):
        if ch_name is None:
            await ctx.send("Please specify a voice channel that I should join!")
            return

        voice_channels = []  # holds all voice channels
        channels = ctx.guild.channels  # all channels (including text from) on the server
        for ch in channels:
            if isinstance(ch, channel.VoiceChannel):
                voice_channels.append(ch)  # if a voice channel is found, add it to the list

        # ignore capitalization and remove all non-ascii symbols (such as emojis)
        ch_name = str(ch_name).lower().encode('ascii', 'ignore').decode('ascii').strip()

        # search all voice channels for one that matches the passed name
        for ch in voice_channels:
            normalized_ch = str(ch).lower().encode('ascii', 'ignore').decode('ascii').strip()
            if normalized_ch == ch_name:
                # channel was found, check if already in voice
                if not ctx.guild.voice_client:
                    await ch.connect()
                else:
                    await ctx.guild.voice_client.move_to(ch)
                await auto_leave(ctx.guild.voice_client)
                return

        await ctx.send("Could not find the specified voice channel :frowning:")


class PlayCommands(commands.Cog):
    """
    Contains all commands regarding playback
    """

    @commands.command(name='btts',
                      description="Makes the bot say your message in your voice channel. Type '/btts <your message"
                                  "here> to trigger voice playback",
                      help="Makes the bot say your message in your voice channel")
    async def to_tts(self, ctx):
        global already_playing, last_play, has_played_once, guild_id_to_filenames
        if ctx.author.voice is None and not ctx.guild.voice_client:
            await ctx.send("Your are not currently connected to a voice channel!")
            return

        if not ctx.guild.voice_client:
            ch = ctx.author.voice.channel
            vc = await ch.connect()
        else:
            vc = ctx.guild.voice_client

        text = ctx.message.content[6:]  # remove "/btts " prefix from the actual text

        # only allow up to 1000 characters per message
        if len(text) > 1000:
            text = text[:1000]

        if ctx.guild.id not in guild_id_to_voice_id:
            guild_id_to_voice_id[ctx.guild.id] = voices.BRIAN["name"]

        response = polly_client.synthesize_speech(VoiceId=guild_id_to_voice_id[ctx.guild.id],
                                                  OutputFormat='mp3',
                                                  Text=text,
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
        last_play[ctx.guild.id] = id(obj)  # TODO might have to move this to play function
        has_played_once[ctx.guild.id] = True

        await asyncio.sleep(AUTO_TIMEOUT_SECONDS)

        if last_play[ctx.guild.id] == id(obj):
            await disconnect(vc)

    @commands.command(name='skip', description="Skips the current Voice message",
                      help="Skips the current Voice message")
    async def skip_tts(self, ctx):
        if ctx.guild.voice_client:
            ctx.guild.voice_client.stop()

    @commands.command(name='stop', description="Fully stops playback and deletes the queue",
                      help="Fully stops playback and deletes the queue")
    async def stop_tts(self, ctx):
        global guild_id_to_filenames
        guild_id = ctx.guild.id
        while len(guild_id_to_filenames[guild_id]) > 0:
            ctx.guild.voice_client.stop()


class Info(commands.Cog):
    """
    Contains informative commands
    """

    @commands.command(name='bible', help="Shows a link to a helpful TTS documentation")
    async def bible(self, ctx):
        await ctx.send("Brian TTS for Pepegas:\n "
                       "https://docs.google.com/document/d/1qLKdc3QArtn6PVuGf42EfoMuzvLE_ykWwU1RViEcrbU/edit")

    @commands.command(name='support', help="Support the bot by voting/reviewing it on top.gg")
    async def support(self, ctx):
        await ctx.send("Support the bot by voting/reviewing it on top.gg. Every vote is appreciated! \n"
                       "https://top.gg/bot/860190148179394591")


class VoiceCommands(commands.Cog):
    """
    Contains commands for setting the voice of the bot
    """

    @commands.command(name='voices1', help="Shows the first table of available voices.")
    async def voices_1(self, ctx):
        lines = self.build_voice_table(voices.voices_1)

        await ctx.send(lines)

    @commands.command(name='voices2', help="Shows the second table of available voices.")
    async def voices_2(self, ctx):
        lines = self.build_voice_table(voices.voices_2)

        await ctx.send(lines)

    @commands.command(name='voices3', help="Shows the third table of available voices")
    async def voices_3(self, ctx):
        lines = self.build_voice_table(voices.voices_3)

        await ctx.send(lines)

    @commands.command(name='voices', help="Shows all available voices")
    async def voices(self, ctx):
        lines_1 = self.build_voice_table(voices.voices_1)
        lines_2 = self.build_voice_table(voices.voices_2)
        lines_3 = self.build_voice_table(voices.voices_3)
        await ctx.send(lines_1)
        await ctx.send(lines_2)
        await ctx.send(lines_3)

    @commands.command(name='setvoice', help="Sets the voice of the bot. Type '$setvoice <name_of_voice>'.")
    async def set_voice(self, ctx):
        name = ctx.message.content[10:]  # remove "$setvoice " prefix
        name = name.replace('>', '')  # remove '>' in case the user entered the name like "<name>"
        name = name.replace('<', '')  # remove '<' in case the user entered the name like "<name>"
        name = name.capitalize()  # Capitalize the name

        is_available_voice = False
        for voice in voices.all_voices:
            if voice["name"].lower() == name.lower().strip():
                is_available_voice = True
                break

        if not is_available_voice:
            await ctx.send("Sorry, " + name + " is not an available voice!\nUse ```$voices``` to see a list of all "
                                              "available voices, then use ```$setvoice <name>``` to set the voice.")

        else:
            guild_id_to_voice_id[ctx.guild.id] = name
            await ctx.send("TTS voice successfully set to " + name + "!")

    @commands.command(name='currentvoice', help="Shows information about the currently set voice.")
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
            await ctx.send("Current voice info:\n" + "```" +
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

    @commands.command(name='broadcast')
    @commands.is_owner()
    async def broadcast(self, ctx):
        msg = ctx.message.content[11:]
        for server in bot.guilds:
            for ch in server.text_channels:
                try:
                    await ch.send(msg)
                except Exception:
                    continue
                else:
                    break

    @commands.command(name='broadcastpreview')
    @commands.is_owner()
    async def broadcast_preview(self, ctx):
        msg = ctx.message.content[18:]
        await ctx.send(msg)

    @commands.command(name='servercount')
    @commands.is_owner()
    async def server_count(self, ctx):
        await ctx.send("I'm in " + str(len(bot.guilds)) + " servers!")

# advanced help message by Chris#0001 https://gist.github.com/nonchris/1c7060a14a9d94e7929aa2ef14c41bc2
class Help(commands.Cog):
    """
    Sends this help message
    """

    def __init__(self, bot):
        self.bot = bot

    @commands.command()
    # @commands.bot_has_permissions(add_reactions=True,embed_links=True)
    async def help(self, ctx, *input):
        """Shows all modules of that bot"""

        prefix = '$'
        version = 1.0

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
                                description=f'Use `{prefix}help <module>` to gain more information about that module '
                                            f':smiley:\n')

            # iterating trough cogs, gathering descriptions
            cogs_desc = ''
            for cog in self.bot.cogs:
                if cog == "AdminCommands":
                    continue  # do not display AdminCommands in help menu

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
                                    If you enjoy the bot, please leave a rating on top.gg: \
                                    https://top.gg/bot/860190148179394591 \n\
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


bot.remove_command('help')  # remove default help command to make room for the custom one
bot.add_cog(PlayCommands())
bot.add_cog(ChannelCommands())
bot.add_cog(VoiceCommands())
bot.add_cog(Info())
bot.add_cog(AdminCommands())
bot.add_cog(Help(bot))

bot.run(TOKEN)
