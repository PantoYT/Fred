import discord
from discord.ext import commands, tasks
import aiohttp
import os
from dotenv import load_dotenv
from datetime import datetime, timedelta
import json
import asyncio
import pytz

# -------------------------------
# Load environment
# -------------------------------
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID"))
EPIC_API_KEY = os.getenv("EPIC_API_KEY")
EPIC_API_URL = "https://epic-games-store-free-games.p.rapidapi.com/free?country=PL"
HEADERS = {
    "x-rapidapi-key": EPIC_API_KEY,
    "x-rapidapi-host": "epic-games-store-free-games.p.rapidapi.com"
}

POSTED_FILE = "posted_games.json"
CHANNEL_NAME = "free-games"
CET = pytz.timezone('Europe/Warsaw')

# -------------------------------
# Discord bot setup
# -------------------------------
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="/", intents=intents)

pending_confirmations = {}
pending_channel_creation = {}

# -------------------------------
# Sync slash commands
# -------------------------------
@bot.event
async def on_ready():
    global last_daily_run
    now = datetime.now(CET)
    print(f"Bot logged in as {bot.user} at {now.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Connected to {len(bot.guilds)} guilds")
    
    detected_channels = []
    guilds_without_channel = []
    
    for guild in bot.guilds:
        channel_found = False
        for channel in guild.text_channels:
            if channel.name == CHANNEL_NAME:
                detected_channels.append(channel.id)
                channel_found = True
                print(f"Found channel '{CHANNEL_NAME}' in {guild.name} (ID: {channel.id})")
                break
        
        if not channel_found:
            guilds_without_channel.append(guild)
            print(f"WARNING: No '{CHANNEL_NAME}' channel in {guild.name}")
    
    if detected_channels:
        print(f"Total channels detected: {len(detected_channels)}")
    
    for guild in guilds_without_channel:
        try:
            target_channel = guild.system_channel or guild.text_channels[0] if guild.text_channels else None
            
            if target_channel:
                embed = discord.Embed(
                    title="Fred Setup Required",
                    description=f"Fred needs a channel named `{CHANNEL_NAME}` to post Epic Games updates.",
                    color=0xFF6B6B
                )
                embed.add_field(
                    name="Option 1: Auto-create",
                    value=f"Use `/confirmchannel` and Fred will create the channel for you.",
                    inline=False
                )
                embed.add_field(
                    name="Option 2: Manual",
                    value=f"Create a channel named `{CHANNEL_NAME}` yourself.",
                    inline=False
                )
                await target_channel.send(embed=embed)
        except Exception as e:
            print(f"Could not send setup message to {guild.name}: {e}")
    
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash commands")
    except Exception as e:
        print(f"Failed to sync commands: {e}")
    
    await bot.change_presence(activity=discord.Activity(
        type=discord.ActivityType.watching,
        name="Watching free games | /commands"
    ))
    
    today_str = str(now.date())
    target_time = datetime.now(CET).replace(hour=17, minute=1, second=0, microsecond=0).time()

    if last_daily_run != today_str and now.time() >= target_time:
        print("Running late start check...")
        await run_check()
    
    daily_check.start()

# -------------------------------
# Load or initialize posted games
# -------------------------------
try:
    with open(POSTED_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
        posted_games = data.get("current", [])
        posted_upcoming = data.get("upcoming", [])
        last_daily_run = data.get("last_daily_run", None)
except (FileNotFoundError, json.JSONDecodeError):
    posted_games = []
    posted_upcoming = []
    last_daily_run = None

def save_posted():
    with open(POSTED_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "current": posted_games,
            "upcoming": posted_upcoming,
            "last_daily_run": last_daily_run
        }, f, ensure_ascii=False, indent=2)

# -------------------------------
# Helper: get all free-games channels
# -------------------------------
def get_free_game_channels():
    """Find all channels named 'free-games' across all guilds"""
    channels = []
    for guild in bot.guilds:
        for channel in guild.text_channels:
            if channel.name == CHANNEL_NAME:
                channels.append(channel)
    return channels
async def fetch_games():
    try:
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(headers=HEADERS, timeout=timeout) as session:
            async with session.get(EPIC_API_URL) as resp:
                if resp.status != 200:
                    print(f"API error: {resp.status}")
                    return None
                return await resp.json()
    except Exception as e:
        print(f"Error fetching games: {e}")
        return None

# -------------------------------
# Helper: create embeds for games
# -------------------------------
def make_embeds(games, ctx_mention=None, upcoming=False, wide_image=False):
    embeds = []
    for g in games:
        title = g.get("title", "Unknown Game")
        desc = g.get("description", "No description")
        seller = g.get("seller", {}).get("name", "Unknown")
        slug = g.get("urlSlug")
        url = f"https://www.epicgames.com/store/p/{slug}" if slug else "No link"

        date_field = None
        if upcoming:
            date_field = g.get("effectiveDate", "Unknown start")
            if date_field:
                try:
                    dt = datetime.fromisoformat(date_field.replace("Z", "+00:00")) + timedelta(hours=1)
                    date_field = dt.strftime("%Y-%m-%d %H:%M")
                except:
                    pass
        else:
            promos = g.get("promotions", {}).get("promotionalOffers", [])
            if promos and promos[0].get("promotionalOffers"):
                end_raw = promos[0]["promotionalOffers"][0].get("endDate")
                if end_raw:
                    try:
                        dt = datetime.fromisoformat(end_raw.replace("Z", "+00:00")) + timedelta(hours=1)
                        date_field = dt.strftime("%Y-%m-%d %H:%M")
                    except:
                        pass

        image_url = None
        thumbnail_url = None
        
        for img in g.get("keyImages", []):
            img_type = img.get("type")
            if wide_image and img_type in ["DieselStoreFrontWide", "OfferImageWide"]:
                image_url = img.get("url")
                break
            elif img_type == "Thumbnail":
                thumbnail_url = img.get("url")
        
        if wide_image and not image_url:
            image_url = thumbnail_url

        embed = discord.Embed(title=title, description=desc, color=0x1E3A8A)
        embed.add_field(name="Seller", value=seller, inline=True)
        
        if date_field:
            if upcoming:
                embed.add_field(name="Available From", value=date_field, inline=True)
            else:
                embed.add_field(name="Available Until", value=date_field, inline=True)
        
        if wide_image and image_url:
            embed.set_image(url=image_url)
        elif thumbnail_url:
            embed.set_thumbnail(url=thumbnail_url)
            
        if ctx_mention:
            embed.set_footer(text=f"Checked by {ctx_mention}")
            
        embeds.append(embed)
    return embeds

# -------------------------------
# Helper: check if games are same
# -------------------------------
def are_games_same(new_games, old_games):
    new_titles = {g.get("title") for g in new_games}
    old_titles = {g.get("title") for g in old_games}
    return new_titles == old_titles

# -------------------------------
# Core: run the check
# -------------------------------
async def run_check(ctx_mention=None, force=False, interaction_channel=None):
    global last_daily_run, posted_games, posted_upcoming
    
    data = await fetch_games()
    if not data:
        print("Failed to fetch games")
        return False

    if interaction_channel:
        channels = [interaction_channel]
    else:
        channels = get_free_game_channels()
    
    if not channels:
        print("No channels found to post to")
        return False

    current_games = data.get("currentGames", [])
    next_games = data.get("nextGames", [])

    if are_games_same(current_games, posted_games) and not force:
        if ctx_mention and interaction_channel:
            pending_confirmations[ctx_mention] = datetime.now(CET) + timedelta(minutes=1)
            await interaction_channel.send(f"{ctx_mention}, games are the same as last check. Use /confirm within 1 min to see them again.")
        return True

    posted_games = current_games.copy()

    new_upcoming = [g for g in next_games if g.get("title") not in [u.get("title") for u in posted_upcoming]]
    posted_upcoming.extend(new_upcoming)

    now = datetime.now(CET)
    last_daily_run = str(now.date())
    save_posted()

    embeds_current = make_embeds(current_games, ctx_mention=ctx_mention, upcoming=False, wide_image=True)
    embeds_upcoming = make_embeds(new_upcoming, ctx_mention=ctx_mention, upcoming=True, wide_image=True)

    for channel in channels:
        try:
            if embeds_current:
                await channel.send("**Current Free Games:**")
                for e in embeds_current:
                    await channel.send(embed=e)

            if embeds_upcoming:
                await channel.send("**Upcoming Free Games:**")
                for e in embeds_upcoming:
                    await channel.send(embed=e)
            
            print(f"Posted to {channel.guild.name} - #{channel.name}")
        except Exception as e:
            print(f"Failed to post to {channel.guild.name} - #{channel.name}: {e}")

    return True

# -------------------------------
# Commands
# -------------------------------
@bot.tree.command(name="commands", description="Show all available commands")
async def commands_slash(interaction: discord.Interaction):
    embed = discord.Embed(
        title="Fred - Epic Games Tracker",
        description="Track free Epic Games Store games automatically",
        color=0x1E3A8A
    )
    embed.add_field(
        name="/showcurrent",
        value="Display current free games",
        inline=False
    )
    embed.add_field(
        name="/showupcoming",
        value="Display upcoming free games",
        inline=False
    )
    embed.add_field(
        name="/nextcheck",
        value="Show time until next automatic check",
        inline=False
    )
    embed.add_field(
        name="/confirm",
        value="Show games again if they haven't changed",
        inline=False
    )
    embed.add_field(
        name="/confirmchannel",
        value="Create a free-games channel (requires Manage Channels permission)",
        inline=False
    )
    embed.add_field(
        name="/commands",
        value="Show this help menu",
        inline=False
    )
    embed.add_field(
        name="/getgame",
        value="Manually check for new free games (owner only)",
        inline=False
    )
    embed.add_field(
        name="/shutdown",
        value="Shut down the bot (owner only)",
        inline=False
    )
    embed.set_footer(text="Daily automatic check at 17:01 CET")
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="showcurrent", description="Display current free games")
async def showcurrent_slash(interaction: discord.Interaction):
    await interaction.response.defer()
    embeds = make_embeds(posted_games, ctx_mention=interaction.user.mention, upcoming=False, wide_image=True)
    if embeds:
        await interaction.followup.send("**Current Free Games:**")
        for e in embeds:
            await interaction.followup.send(embed=e)
    else:
        await interaction.followup.send("No current games to display.")

@bot.tree.command(name="showupcoming", description="Display upcoming free games")
async def showupcoming_slash(interaction: discord.Interaction):
    await interaction.response.defer()
    embeds = make_embeds(posted_upcoming, ctx_mention=interaction.user.mention, upcoming=True, wide_image=True)
    if embeds:
        await interaction.followup.send("**Upcoming Free Games:**")
        for e in embeds:
            await interaction.followup.send(embed=e)
    else:
        await interaction.followup.send("No upcoming games to display.")

@bot.tree.command(name="nextcheck", description="Show time until next automatic check")
async def nextcheck_slash(interaction: discord.Interaction):
    now = datetime.now(CET)
    target = now.replace(hour=17, minute=1, second=0, microsecond=0)
    
    if now >= target:
        target += timedelta(days=1)
    
    time_diff = target - now
    hours = int(time_diff.total_seconds() // 3600)
    minutes = int((time_diff.total_seconds() % 3600) // 60)
    
    embed = discord.Embed(
        title="Next Automatic Check",
        description=f"The next automatic check will run at **{target.strftime('%H:%M')} CET**",
        color=0x1E3A8A
    )
    embed.add_field(
        name="Time Remaining",
        value=f"{hours} hours and {minutes} minutes",
        inline=False
    )
    embed.add_field(
        name="Date",
        value=target.strftime('%Y-%m-%d'),
        inline=False
    )
    
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="getgame", description="Manually check for new free games (owner only)")
async def getgame_slash(interaction: discord.Interaction):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("This command is owner-only.", ephemeral=True)
        return
    
    await interaction.response.send_message(f"Manual check triggered by {interaction.user.mention}")
    result = await run_check(ctx_mention=interaction.user.mention, force=False, interaction_channel=interaction.channel)
    if not result:
        await interaction.followup.send("Failed to fetch games.")

@bot.tree.command(name="confirm", description="Confirm to see games again if they're the same")
async def confirm_slash(interaction: discord.Interaction):
    await interaction.response.defer()
    now = datetime.now(CET)
    expiry = pending_confirmations.get(interaction.user.mention)
    if expiry and now <= expiry:
        pending_confirmations.pop(interaction.user.mention)
        embeds = make_embeds(posted_games, ctx_mention=interaction.user.mention, upcoming=False, wide_image=True)
        if embeds:
            await interaction.followup.send("**Current Free Games:**")
            for e in embeds:
                await interaction.followup.send(embed=e)
        else:
            await interaction.followup.send("No current games to display.")
    else:
        await interaction.followup.send("No pending confirmation or it expired.")

@bot.tree.command(name="confirmchannel", description="Create a free-games channel for Fred")
async def confirmchannel_slash(interaction: discord.Interaction):
    guild = interaction.guild
    
    if not guild:
        await interaction.response.send_message("This command must be used in a server.", ephemeral=True)
        return
    
    for channel in guild.text_channels:
        if channel.name == CHANNEL_NAME:
            await interaction.response.send_message(f"A channel named `{CHANNEL_NAME}` already exists in this server.", ephemeral=True)
            return
    
    if not guild.me.guild_permissions.manage_channels:
        await interaction.response.send_message("Fred doesn't have permission to create channels. Please give Fred the 'Manage Channels' permission or create the channel manually.", ephemeral=True)
        return
    
    if not interaction.user.guild_permissions.manage_channels:
        await interaction.response.send_message("You need 'Manage Channels' permission to use this command.", ephemeral=True)
        return
    
    try:
        new_channel = await guild.create_text_channel(
            name=CHANNEL_NAME,
            topic="Free games from Epic Games Store - Updated daily by Fred"
        )
        
        embed = discord.Embed(
            title="Channel Created Successfully",
            description=f"Fred will now post Epic Games updates in {new_channel.mention}",
            color=0x4CAF50
        )
        embed.add_field(
            name="Daily Updates",
            value="Fred will automatically check for new games at 17:01 CET every day.",
            inline=False
        )
        embed.add_field(
            name="Commands",
            value="Use `/commands` to see all available commands.",
            inline=False
        )
        
        await interaction.response.send_message(embed=embed)
        
        welcome_embed = discord.Embed(
            title="Welcome to Free Games Updates",
            description="Fred will post Epic Games Store free game updates here daily at 17:01 CET.",
            color=0x1E3A8A
        )
        welcome_embed.add_field(
            name="Commands",
            value="Use `/showcurrent` to see current free games\nUse `/showupcoming` to see upcoming games\nUse `/commands` for all commands",
            inline=False
        )
        await new_channel.send(embed=welcome_embed)
        
        print(f"Created channel '{CHANNEL_NAME}' in {guild.name}")
        
    except Exception as e:
        await interaction.response.send_message(f"Failed to create channel: {e}", ephemeral=True)
        print(f"Error creating channel in {guild.name}: {e}")

@bot.tree.command(name="shutdown", description="Shutdown the bot (owner only)")
async def shutdown_slash(interaction: discord.Interaction):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("You don't have permission.", ephemeral=True)
        return
    await interaction.response.send_message("Shutting down...")
    await bot.close()

# -------------------------------
# Daily scheduled task at 17:01 CET
# -------------------------------
@tasks.loop(hours=24)
async def daily_check():
    global last_daily_run
    now = datetime.now(CET)
    today_str = str(now.date())
    if last_daily_run != today_str:
        print(f"Running daily check at {now.strftime('%Y-%m-%d %H:%M:%S')}")
        await run_check()
        next_run = now + timedelta(hours=24)
        print(f"Next daily check scheduled for {next_run.strftime('%Y-%m-%d %H:%M:%S')}")

@daily_check.before_loop
async def before_daily_check():
    await bot.wait_until_ready()
    now = datetime.now(CET)
    target = now.replace(hour=17, minute=1, second=0, microsecond=0)
    if now >= target:
        target += timedelta(days=1)
    sleep_seconds = (target - now).total_seconds()
    print(f"Daily check will start in {sleep_seconds/3600:.2f} hours at {target.strftime('%H:%M:%S')}")
    await asyncio.sleep(sleep_seconds)

# -------------------------------
# Run the bot
# -------------------------------
if __name__ == "__main__":
    bot.run(TOKEN)