import discord
from discord import app_commands
from discord.ui import Modal, TextInput
import os
import json
import asyncio
import re
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
CLIENT_ID = int(os.getenv("CLIENT_ID"))
LINKED_ROLE_ID = 1472751333286350981

DB_FILE = "emails.json"

# ─── Embed helper ─────────────────────────────────────────────────────────────

def make_embed(description: str, success: bool = True) -> discord.Embed:
    color = discord.Color.green() if success else discord.Color.red()
    embed = discord.Embed(description=description, color=color)
    return embed

# ─── Email store ──────────────────────────────────────────────────────────────

def load_emails():
    if not os.path.exists(DB_FILE):
        return {}
    with open(DB_FILE) as f:
        return json.load(f)

def save_emails(data):
    with open(DB_FILE, "w") as f:
        json.dump(data, f, indent=2)

# ─── Duration parser ──────────────────────────────────────────────────────────

UNIT_SECONDS = {
    "s":  1,
    "m":  60,
    "h":  3_600,
    "d":  86_400,
    "w":  604_800,
    "mo": 2_592_000,
    "y":  31_536_000,
}

def parse_duration(text: str):
    pattern = r"(\d+)\s*(y|mo|w|d|h|m|s)"
    matches = re.findall(pattern, text.strip().lower())
    if not matches:
        raise ValueError("No valid duration found")

    total = 0
    parts = []
    unit_labels = {
        "s": "second", "m": "minute", "h": "hour",
        "d": "day", "w": "week", "mo": "month", "y": "year"
    }

    for amount_str, unit in matches:
        amount = int(amount_str)
        total += amount * UNIT_SECONDS[unit]
        label = unit_labels[unit]
        parts.append(f"{amount} {label}{'s' if amount != 1 else ''}")

    return total, ", ".join(parts)

# ─── Bot setup ────────────────────────────────────────────────────────────────

intents = discord.Intents.default()
intents.members = True
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

# ─── /setup ───────────────────────────────────────────────────────────────────

class EmailModal(Modal, title="Link Your Email"):
    email = TextInput(
        label="What is your email?",
        placeholder="you@example.com",
        required=True,
        max_length=200,
    )

    async def on_submit(self, interaction: discord.Interaction):
        raw = self.email.value.strip()
        if not re.match(r"^[^\s@]+@[^\s@]+\.[^\s@]+$", raw):
            await interaction.response.send_message(
                embed=make_embed("❌ That doesn't look like a valid email. Please try `/setup` again.", success=False),
                ephemeral=True,
            )
            return

        emails = load_emails()
        emails[str(interaction.user.id)] = {
            "email": raw,
            "linked_at": interaction.created_at.isoformat(),
        }
        save_emails(emails)

        await interaction.response.send_message(
            embed=make_embed(f"✅ Your email **{raw}** has been linked to your account!"),
            ephemeral=True,
        )

@tree.command(name="setup", description="Link your email to your Discord account")
async def setup(interaction: discord.Interaction):
    member = interaction.guild.get_member(interaction.user.id)
    role_ids = [r.id for r in member.roles]

    if LINKED_ROLE_ID not in role_ids:
        await interaction.response.send_message(
            embed=make_embed("❌ You don't have permission to use this command.", success=False),
            ephemeral=True,
        )
        return

    await interaction.response.send_modal(EmailModal())

# ─── /role ────────────────────────────────────────────────────────────────────

@tree.command(name="role", description="Temporarily give a user the linked role")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(
    user="The user to give the role to",
    duration="How long? e.g. 1d, 3w, 2h30m, 1mo, 1y — any combination works",
)
async def role_cmd(interaction: discord.Interaction, user: discord.Member, duration: str):
    try:
        total_seconds, friendly = parse_duration(duration)
    except ValueError:
        await interaction.response.send_message(
            embed=make_embed("❌ Invalid duration. Try something like `1d`, `3w`, `2h30m`, `1mo`, `1y2w3d`.", success=False),
            ephemeral=True,
        )
        return

    guild = interaction.guild
    role = guild.get_role(LINKED_ROLE_ID)

    if role is None:
        await interaction.response.send_message(
            embed=make_embed("❌ Could not find the role. Make sure the role ID is correct.", success=False),
            ephemeral=True,
        )
        return

    try:
        await user.add_roles(role, reason=f"Temp role via /role — {friendly}")
    except discord.Forbidden:
        await interaction.response.send_message(
            embed=make_embed("❌ I don't have permission to assign that role. Make sure my role is above it in the hierarchy.", success=False),
        )
        return

    embed = discord.Embed(color=discord.Color.green())
    embed.add_field(name="User", value=user.mention, inline=True)
    embed.add_field(name="Role", value=role.mention, inline=True)
    embed.add_field(name="Duration", value=friendly, inline=True)
    embed.set_footer(text="Role will be automatically removed when the time is up.")

    await interaction.response.send_message(
        embed=embed,
        allowed_mentions=discord.AllowedMentions(users=True, roles=True),
    )

    # Schedule removal
    async def remove_role_later():
        await asyncio.sleep(total_seconds)
        try:
            refreshed = guild.get_member(user.id)
            if refreshed and role in refreshed.roles:
                await refreshed.remove_roles(role, reason="Temp role expired")
                try:
                    await user.send(
                        embed=make_embed(f"⏰ Your temporary role **{role.name}** in **{guild.name}** has expired after {friendly}.")
                    )
                except discord.Forbidden:
                    pass
        except Exception as e:
            print(f"Error removing temp role: {e}")

    asyncio.create_task(remove_role_later())

@role_cmd.error
async def role_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message(
            embed=make_embed("❌ Only admins can use this command.", success=False),
            ephemeral=True,
        )

# ─── On ready ────────────────────────────────────────────────────────────────

@client.event
async def on_ready():
    await tree.sync()
    print(f"✅ Logged in as {client.user} — slash commands synced.")

client.run(TOKEN)
