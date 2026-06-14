# general lib imports
import asyncio
import random
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from constants import TOKEN, MODMAIL_FORUM_ID, PREFIX, ANONYMOUS_REPLIES

discord.utils.setup_logging()

intents = discord.Intents.default() # No privileged intents, no guarantee we would be approved for them.
allowed_mentions = discord.AllowedMentions(everyone=False, roles=False)
bot = commands.Bot(allowed_mentions=allowed_mentions, intents=intents, command_prefix=PREFIX)

bot.ready = False
bot.modmail_forum = None
bot.modmail_locks = set()

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

async def create_modmail_thread(user: discord.User, message=discord.Message) -> discord.Thread:
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

    await message.add_reaction('\N{WHITE HEAVY CHECK MARK}')
    return thread

async def send_message_in_modmail_thread(thread: discord.Thread, message: discord.Message):
    message_embed, files = await setup_message_contents(message=message)
    await thread.send(content=str(message.author.id), embed=message_embed, files=files)
    await message.add_reaction('\N{WHITE HEAVY CHECK MARK}')

async def get_modmail_thread_author(thread: discord.Thread) -> discord.User:
    username, user_id = thread.name.rsplit(" - ", 1)

    user = await bot.fetch_user(int(user_id))
    return user

async def handle_modmail_dm(message: discord.Message):
    user = message.author
    if user.id in bot.modmail_locks:
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

# reading this shit makes me wanna kill myself, holy fuck
@app_commands.describe(reply_message="The message to reply with", anonymous_reply="Whether to hide the replier. Defaults to the value in constants.py")
@app_commands.guild_only()
@bot.tree.command(name="reply", description="Reply to Mod-Mail message in the current thread")
async def reply_command(interaction: discord.Interaction, reply_message: str, anonymous_reply: bool = ANONYMOUS_REPLIES, 
    file1: discord.Attachment = None, file2: discord.Attachment = None, file3: discord.Attachment = None, file4: discord.Attachment = None, 
    file5: discord.Attachment = None, file6: discord.Attachment = None, file7: discord.Attachment = None, file8: discord.Attachment = None, 
    file9: discord.Attachment = None, file10: discord.Attachment = None):
    if not isinstance(interaction.channel, discord.Thread) or interaction.channel.parent_id != bot.modmail_forum.id: # just an extra check to prevent it from being used in other forums
        await interaction.response.send_message("This command can only be used in Mod-Mail threads.", ephemeral=True)
        return
    await interaction.response.defer() # we need this or we only get like 3 seconds, with this we get up to 15 mins

    discord_files = []
    for f in (file1, file2, file3, file4, file5, file6, file7, file8, file9, file10):
        if f:
            discord_files.append(await f.to_file())

    author = await get_modmail_thread_author(interaction.channel)
    content = f"Staff reply: {reply_message}" if anonymous_reply else f"{interaction.user.mention}: {reply_message}"
    await author.send(content=content, files=discord_files)
    try:
        await interaction.followup.send("Reply sent!")
    except discord.Forbidden:
        await interaction.followup.send(f"Failed to DM {author.mention}.")

@app_commands.guild_only()
@bot.tree.command(name="close", description="Close a Mod-Mail thread")
async def close_command(interaction: discord.Interaction):
    if not isinstance(interaction.channel, discord.Thread) or interaction.channel.parent_id != bot.modmail_forum.id: # just an extra check to prevent it from being used in other forums
        await interaction.response.send_message("This command can only be used in Mod-Mail threads.", ephemeral=True)
        return
    await interaction.response.defer() # we need this or we only get like 3 seconds, with this we get up to 15 mins

    now = datetime.now(timezone.utc)
    timestamp = now.strftime("%Y-%m-%d %H:%M UTC")
    
    # lock the thread, simple stuff
    await interaction.channel.edit(locked=True, archived=True, name=f"{interaction.channel.name} (ARCHIVED {timestamp})") # max theoretical is 84-88 chars. we're safe.
    await interaction.followup.send("Successfully closed thread!")

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

async def main():
    async with bot:
        await bot.start(TOKEN)

asyncio.run(main())