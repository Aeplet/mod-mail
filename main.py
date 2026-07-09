# general lib imports
# todo: split this into multiple files, this is pretty messy... but it works for now.
import random
import sys
from datetime import datetime, timezone

# discord.py
import discord
from discord import app_commands
from discord.ext import commands

# other stuff such as sqlite3
import sqlite3

# constants
from constants import TOKEN, MODMAIL_FORUM_ID, PREFIX, ANONYMOUS_REPLIES, PLAYING_MESSAGE

intents = discord.Intents.default() # No privileged intents, no guarantee we would be approved for them.
allowed_mentions = discord.AllowedMentions(everyone=False, roles=False)
bot = commands.Bot(allowed_mentions=allowed_mentions, intents=intents, command_prefix=PREFIX, activity=discord.Game(PLAYING_MESSAGE))

bot.ready = False
bot.modmail_forum = None
bot.modmail_locks = set()

database = sqlite3.connect("modmail_data.sqlite")
with database:
    if database.execute('PRAGMA user_version').fetchone()[0] == 0:
        print("Setting up database")
        database.execute('PRAGMA application_id = 0x4D6F644D')  # ModM
        database.execute('PRAGMA user_version = 1')
        
        try:
            with open('schema.sql', 'r', encoding='utf-8') as f:
                database.executescript(f.read())
        except FileNotFoundError:
            print("schema.sql not found!")
            sys.exit(1)

#### DATABASE FUNCTIONS

def is_ignored(user_id: int):
    with database:
        return database.execute('SELECT quiet, reason FROM ignored WHERE user_id = ?', (user_id,)).fetchone()

def add_ignore(user_id: int, reason: str = None, is_quiet: bool = False) -> bool:
    try:
        with database:
            database.execute('INSERT INTO ignored VALUES (?, ?, ?)', (user_id, is_quiet, reason))
            return True
    except sqlite3.IntegrityError:
        return False

def remove_ignore(user_id: int) -> int:
    with database:
        return database.execute('DELETE FROM ignored WHERE user_id = ?', (user_id,)).rowcount

#### BOT FUNCTIONS

async def shutdown_bot():
    await bot.close()

# I just really liked this from discord-mod-mail: https://github.com/ihaveamac/discord-mod-mail/blob/878eb12cb2c0307f31de0f1c70d7f0b517cb5587/run.py#L130-L135
def generate_user_color(user_id: int) -> discord.Color:
    random.seed(user_id)
    c_r = random.randint(0, 255)
    c_g = random.randint(0, 255)
    c_b = random.randint(0, 255)
    return discord.Color((c_r << 16) + (c_g << 8) + c_b)

def generate_message_embed(message: discord.Message) -> discord.Embed:
    # https://github.com/ihaveamac/discord-mod-mail/blob/878eb12cb2c0307f31de0f1c70d7f0b517cb5587/run.py#L179 same thing as generate_user_color
    embed = discord.Embed(color=generate_user_color(int(message.author.id)), description=message.content)
    embed.set_author(name=message.author.name, icon_url=message.author.avatar.url if message.author.avatar else message.author.default_avatar.url)
    return embed

async def setup_message_contents(message: discord.Message) -> tuple[discord.Embed, list[discord.File]]:
    files = []
    message_embed = generate_message_embed(message=message)
    for attachment in message.attachments:
        files.append(await attachment.to_file())
    
    return message_embed, files

async def get_active_modmail_threads():
    active_guild_threads = await bot.modmail_forum.guild.active_threads()

    return [thread for thread in active_guild_threads if thread.parent_id == bot.modmail_forum.id]

async def user_has_open_thread(user: discord.User) -> tuple[bool, discord.Thread | None]:
    active_threads = await get_active_modmail_threads()

    for thread in active_threads:
        if thread.name.endswith(str(user.id)): # i think there is a better function for this?
            return True, thread
    
    return False, None # we didn't find a thread for them.

async def create_modmail_thread(user: discord.User, message: discord.Message) -> discord.Thread:
    print(f"Creating Mod-Mail thread for user {user} ({user.id})")
    message_embed, files = await setup_message_contents(message=message)
    thread = await bot.modmail_forum.create_thread(
        name=f"{user} - {user.id}",
        auto_archive_duration=10080, # in minutes. 10080 is 7 days, also acceptable is 60 (1 hour), 1440 (24 hours), 72 hours (3 days)
        reason=f"User {user.id} opened a Mod-Mail thread",
        slowmode_delay=None,
        content=str(user.id), # best option for now, we have to include a non-empty content
        embed=message_embed,
        files=files
    )

    await message.add_reaction("\N{INCOMING ENVELOPE}")
    return thread

async def send_message_in_modmail_thread(thread: discord.Thread, message: discord.Message):
    message_embed, files = await setup_message_contents(message=message)
    await thread.send(content=str(message.author.id), embed=message_embed, files=files)
    await message.add_reaction("\N{INCOMING ENVELOPE}")

async def get_modmail_thread_author(thread: discord.Thread) -> discord.User:
    username, user_id = thread.name.rsplit(" - ", 1)

    user = await bot.fetch_user(int(user_id))
    return user

async def handle_modmail_dm(message: discord.Message):
    user = message.author
    
    if user.id in bot.modmail_locks or is_ignored(user.id): # when to not handle the DM
        return
    
    bot.modmail_locks.add(user.id)

    try:
        has_open_thread, thread = await user_has_open_thread(user=user)
        if has_open_thread:
            await send_message_in_modmail_thread(thread=thread, message=message)
            return
        else:
            thread = await create_modmail_thread(user=user, message=message)
            return
    finally:
        bot.modmail_locks.discard(user.id)

def modmail_thread_only():
    async def predicate(interaction: discord.Interaction):
        if not isinstance(interaction.channel, discord.Thread) or interaction.channel.parent_id != bot.modmail_forum.id:
            await interaction.response.send_message("This command can only be used in Mod-Mail threads.", ephemeral=True)
            return False

        return True

    return app_commands.check(predicate)

def modmail_command():
    return app_commands.guild_only()(app_commands.guild_install()(modmail_thread_only()))

# reading this shit makes me wanna kill myself, holy fuck (should we use a modal for reply?)
@modmail_command()
@app_commands.describe(reply_message="The message to reply with", anonymous_reply=f"Whether to hide the replier. Defaults to {ANONYMOUS_REPLIES}")
@bot.tree.command(name="reply", description="Reply to Mod-Mail message in the current thread")
async def reply_command(interaction: discord.Interaction, reply_message: str, anonymous_reply: bool = ANONYMOUS_REPLIES, 
    file1: discord.Attachment = None, file2: discord.Attachment = None, file3: discord.Attachment = None, file4: discord.Attachment = None, 
    file5: discord.Attachment = None, file6: discord.Attachment = None, file7: discord.Attachment = None, file8: discord.Attachment = None, 
    file9: discord.Attachment = None, file10: discord.Attachment = None):
    await interaction.response.defer() # we need this or we only get like 3 seconds, with this we get up to 15 mins

    discord_files = []
    for file in (file1, file2, file3, file4, file5, file6, file7, file8, file9, file10):
        if file:
            discord_files.append(await file.to_file())

    author = await get_modmail_thread_author(interaction.channel)
    content = f"Staff reply: {reply_message}" if anonymous_reply else f"{interaction.user.mention}: {reply_message}"
    # todo: reply function?
    try:
        await author.send(content=content, files=discord_files)
        await interaction.followup.send(f"Reply sent! Message: `{reply_message}`", files=discord_files)
    except discord.Forbidden:
        await interaction.followup.send(f"Failed to DM {author.mention}.")

@modmail_command()
@bot.tree.command(name="close", description="Close a Mod-Mail thread")
async def close_command(interaction: discord.Interaction):
    await interaction.response.defer() # we need this or we only get like 3 seconds, with this we get up to 15 mins

    now = datetime.now(timezone.utc)
    timestamp = now.strftime("%Y-%m-%d %H:%M UTC")
    
    # lock the thread, simple stuff
    await interaction.channel.edit(locked=True, archived=True, name=f"{interaction.channel.name} (ARCHIVED {timestamp})") # max theoretical is 84-88 chars. we're safe.
    await interaction.followup.send("Successfully closed thread!")

@app_commands.describe(user="The user to ignore", quiet="Whether to silently ignore the user", reason="The reason for ignoring the user")
@app_commands.guild_only()
@app_commands.guild_install()
@bot.tree.command(name="ignore", description="Ignore a user from Mod-Mail")
async def ignore_command(interaction: discord.Interaction, user: discord.User, quiet: bool, reason: str = None):
    await interaction.response.defer() # we need this or we only get like 3 seconds, with this we get up to 15 mins

    ignored = add_ignore(user_id=user.id, reason=reason, is_quiet=quiet)
    if not ignored:
        await interaction.followup.send("Failed to ignore user. User already ignored possibly?") # should show an error reason from sqlite3?
        return
    
    if not quiet:
        try:
            await user.send(f"Your messages are being ignored by staff. {f'Reason: {reason}' if reason else ''}")
        except discord.Forbidden:
            await interaction.followup.send(f"Failed to DM {user.mention}.")

    await interaction.followup.send(f"Successfully ignored {user.mention} ({user.id})!")

@app_commands.describe(user="The user to unignore")
@app_commands.guild_only()
@app_commands.guild_install()
@bot.tree.command(name="unignore", description="Unignore a user from Mod-Mail")
async def unignore_command(interaction: discord.Interaction, user: discord.User):
    await interaction.response.defer() # we need this or we only get like 3 seconds, with this we get up to 15 mins

    ignored = remove_ignore(user_id=user.id)
    if not ignored:
        await interaction.followup.send("User is not ignored!")
        return
    try:
        await user.send(f"Your messages are no longer being ignored by staff.")
    except discord.Forbidden:
        await interaction.followup.send(f"Failed to DM {user.mention}.")
    
    await interaction.followup.send(f"Successfully unignored {user.mention} ({user.id})!")

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} ({bot.user.id})")
    if not bot.ready:
        bot.ready = True
        await bot.tree.sync()
        try:
            bot.modmail_forum = await bot.fetch_channel(MODMAIL_FORUM_ID)
            if not isinstance(bot.modmail_forum, discord.ForumChannel):
                print(f"{bot.modmail_forum.id} is not a Forum Channel!")
                await shutdown_bot()
                return
            print(f"Successfully loaded forum {bot.modmail_forum.name} ({bot.modmail_forum.id})!")
        except discord.NotFound:
            print(f"Forum with ID {MODMAIL_FORUM_ID} not found.")
            await shutdown_bot()
            return
        except discord.Forbidden:
            print(f"This bot does not have permissions to access the forum {MODMAIL_FORUM_ID}.")

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not isinstance(message.channel, discord.DMChannel):
        return # return instead of anything else like keeping the code in an if loop so it makes our on_message event easier to read

    await handle_modmail_dm(message=message)

bot.run(TOKEN)
