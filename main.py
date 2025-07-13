import discord
from discord.ext import commands
from discord.ui import View, Button, Select, Modal, TextInput
from discord import app_commands, Embed
import os
import asyncio
from datetime import datetime, timedelta
from flask import Flask
from threading import Thread
from dotenv import load_dotenv

load_dotenv()  # Wczytaj zmienne z .env

# --- Intents i bot ---
intents = discord.Intents.default()
intents.members = True
intents.message_content = True
intents.guilds = True

bot = commands.Bot(command_prefix='/', intents=intents)

# --- Stałe ID ---
SUPPORT_CATEGORY_ID = 1384251116493082675
SUPPORT_ANNOUNCE_CHANNEL_ID = 1384272432654844085
MANAGEMENT_ROLE_ID = 1319634655875432519

active_tickets = {}
waiting_for_message = {}

# --- Komenda /ticket-info ---
@bot.tree.command(name="ticket-info", description="Informacje o systemie ticketów")
async def ticket_info(interaction: discord.Interaction):
    embed = discord.Embed(
        title="📩 System Ticketów i Pomocy",
        description="Kliknij **HELP** poniżej, aby rozpocząć.\n\nPo kliknięciu bot wyśle Ci prywatną wiadomość z listą problemów.",
        color=discord.Color.blue()
    )
    view = HelpButtonView()
    await interaction.response.send_message(embed=embed, view=view)

class HelpButtonView(View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(HelpButton())

class HelpButton(Button):
    def __init__(self):
        super().__init__(label="HELP", style=discord.ButtonStyle.danger)

    async def callback(self, interaction: discord.Interaction):
        try:
            await interaction.response.send_message("Sprawdź swoją prywatną wiadomość!", ephemeral=True)
            dm = await interaction.user.create_dm()
            await dm.send("W czym możemy Ci pomóc? Wybierz problem z listy:", view=TicketSelectView(interaction.user))
        except discord.Forbidden:
            await interaction.response.send_message("Nie mogę wysłać Ci wiadomości prywatnej. Ustaw, aby bot mógł pisać do Ciebie DM.", ephemeral=True)

class TicketSelectView(View):
    def __init__(self, user):
        super().__init__(timeout=900)
        self.user = user
        self.add_item(TicketSelect())

class TicketSelect(Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Nie widzę kanałów", description="Problem z widocznością kanałów"),
            discord.SelectOption(label="Jak napisać rekrutację?", description="Pytanie o rekrutację"),
            discord.SelectOption(label="Mam problem z grą", description="Problem techniczny z grą"),
            discord.SelectOption(label="Mam pomysł na serwer", description="Chcę zgłosić pomysł"),
            discord.SelectOption(label="Połącz mnie z asystentem", description="Potrzebuję kontaktu z supportem")
        ]
        super().__init__(placeholder="Wybierz swój problem...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        user = interaction.user
        choice = self.values[0]
        try:
            await interaction.message.delete()
        except:
            pass

        if choice == "Połącz mnie z asystentem":
            guild = next((g for g in bot.guilds if g.get_member(user.id)), None)
            if not guild:
                await interaction.response.send_message("Nie znaleziono serwera.", ephemeral=True)
                return

            category = guild.get_channel(SUPPORT_CATEGORY_ID)
            if not category:
                await interaction.response.send_message("Nie znaleziono kategorii support.", ephemeral=True)
                return

            overwrites = {
                guild.default_role: discord.PermissionOverwrite(read_messages=False),
                guild.get_member(user.id): discord.PermissionOverwrite(read_messages=True, send_messages=True),
                guild.get_role(MANAGEMENT_ROLE_ID): discord.PermissionOverwrite(read_messages=True, send_messages=True),
            }
            channel = await guild.create_text_channel(f'ticket-{user.name}', category=category, overwrites=overwrites)
            active_tickets[user.id] = {
                "type": "ticket",
                "timestamp": datetime.utcnow(),
                "channel": channel,
                "closed": False
            }
            await channel.send(f"{user.mention} otworzył ticket. Management może odpowiedzieć.", view=TicketActionView(channel))
            await interaction.response.send_message(f"Ticket został utworzony: {channel.mention}", ephemeral=True)
            bot.loop.create_task(ticket_inactivity_watchdog(user.id))
        else:
            waiting_for_message[user.id] = choice
            await interaction.response.send_message(
                f"Wybrałeś: **{choice}**.\nNapisz teraz wiadomość w tej prywatnej wiadomości.",
                ephemeral=True
            )

class TicketActionView(View):
    def __init__(self, channel):
        super().__init__(timeout=None)
        self.add_item(ClimbButton(channel))
        self.add_item(RejectButton(channel))

class ClimbButton(Button):
    def __init__(self, channel):
        super().__init__(label="Climb", style=discord.ButtonStyle.success)
        self.channel = channel

    async def callback(self, interaction: discord.Interaction):
        if MANAGEMENT_ROLE_ID not in [role.id for role in interaction.user.roles]:
            await interaction.response.send_message("Nie masz uprawnień.", ephemeral=True)
            return
        await self.channel.send(f"{interaction.user.mention} przejął ticket.")
        await interaction.response.defer()

class RejectButton(Button):
    def __init__(self, channel):
        super().__init__(label="Odrzuć", style=discord.ButtonStyle.danger)
        self.channel = channel

    async def callback(self, interaction: discord.Interaction):
        if MANAGEMENT_ROLE_ID not in [role.id for role in interaction.user.roles]:
            await interaction.response.send_message("Nie masz uprawnień.", ephemeral=True)
            return
        ticket = next((t for t in active_tickets.values() if t["channel"].id == self.channel.id), None)
        if ticket:
            ticket["closed"] = True
            ticket["close_time"] = datetime.utcnow() + timedelta(minutes=5)
        await self.channel.send(f"Ticket zamknięty przez {interaction.user.mention}. Kanał zostanie usunięty za 5 minut.")
        await interaction.response.defer()
        bot.loop.create_task(delete_channel_after_delay(self.channel, 300))

async def delete_channel_after_delay(channel, delay_seconds):
    await asyncio.sleep(delay_seconds)
    try:
        await channel.delete()
    except Exception as e:
        print(f"Nie udało się usunąć kanału: {e}")

async def ticket_inactivity_watchdog(user_id):
    while True:
        await asyncio.sleep(60)
        ticket = active_tickets.get(user_id)
        if not ticket or ticket["closed"]:
            return
        if datetime.utcnow() - ticket["timestamp"] > timedelta(minutes=15):
            try:
                await ticket["channel"].send("Ticket zostanie zamknięty z powodu braku odpowiedzi.")
                ticket["closed"] = True
                ticket["close_time"] = datetime.utcnow() + timedelta(minutes=5)
                bot.loop.create_task(delete_channel_after_delay(ticket["channel"], 300))
            except Exception as e:
                print(f"Błąd przy zamykaniu ticketu: {e}")
            return

@bot.event
async def on_message(message):
    if message.author.bot:
        return
    if isinstance(message.channel, discord.DMChannel):
        if message.author.id in waiting_for_message:
            typ = waiting_for_message.pop(message.author.id)
            if message.author.id in active_tickets:
                active_tickets[message.author.id]["timestamp"] = datetime.utcnow()
            kanal_admin = bot.get_channel(SUPPORT_ANNOUNCE_CHANNEL_ID)
            if not kanal_admin:
                await message.channel.send("Błąd: nie znaleziono kanału administracyjnego.")
                return
            embed = Embed(
                title=f"💬 Nowa wiadomość: {typ}",
                description=message.content,
                color=discord.Color.orange(),
                timestamp=datetime.utcnow()
            )
            embed.set_author(name=str(message.author), icon_url=message.author.display_avatar.url)
            embed.set_footer(text=f"ID: {message.author.id}")
            await kanal_admin.send(embed=embed, view=AdminReplyView(message.author.id))
            await message.channel.send(embed=Embed(description="✅ Twoja wiadomość została przesłana do administracji.", color=discord.Color.green()))
            return
    await bot.process_commands(message)

class AdminReplyView(View):
    def __init__(self, user_id):
        super().__init__(timeout=None)
        self.add_item(AdminReplyButton(user_id))

class AdminReplyButton(Button):
    def __init__(self, user_id):
        super().__init__(label="Odpowiedz", style=discord.ButtonStyle.primary)
        self.user_id = user_id

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(ReplyModal(self.user_id))

class ReplyModal(Modal):
    def __init__(self, user_id):
        super().__init__(title="Odpowiedź dla użytkownika")
        self.user_id = user_id
        self.response_input = TextInput(label="Twoja odpowiedź", style=discord.TextStyle.paragraph, max_length=1000)
        self.add_item(self.response_input)

    async def on_submit(self, interaction: discord.Interaction):
        user = bot.get_user(self.user_id)
        if not user:
            await interaction.response.send_message("Nie można znaleźć użytkownika.", ephemeral=True)
            return
        try:
            embed = Embed(
                title="💬 Odpowiedź od administracji",
                description=self.response_input.value,
                color=discord.Color.blue(),
                timestamp=datetime.utcnow()
            )
            embed.set_footer(text=f"Odpowiedź od: {interaction.user}")
            await user.send(embed=embed)
            await interaction.response.send_message("Odpowiedź została wysłana.", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("Nie można wysłać wiadomości użytkownikowi (DM zablokowane).", ephemeral=True)

@bot.tree.command(name="ogloszenie", description="Wysyła ogłoszenie jako embed")
@app_commands.describe(tresc="Treść ogłoszenia do wysłania")
async def ogloszenie(interaction: discord.Interaction, tresc: str):
    embed = Embed(
        title="📢 Ogłoszenie",
        description=f"📝 {tresc}",
        color=0x2ecc71
    )
    embed.set_footer(text=f"Autor: {interaction.user}", icon_url=interaction.user.avatar.url if interaction.user.avatar else None)
    await interaction.response.send_message(embed=embed)

@bot.event
async def on_message_edit(before, after):
    if after.author.bot:
        return
    for ticket in active_tickets.values():
        if ticket["channel"].id == after.channel.id and not ticket["closed"]:
            if after.author.id in active_tickets:
                active_tickets[after.author.id]["timestamp"] = datetime.utcnow()

# --- Komenda /wiadomosc ---
class MessageModal(discord.ui.Modal, title="Wyślij wiadomość"):
    def __init__(self, interaction: discord.Interaction):
        super().__init__()
        self.interaction = interaction

        self.message_type = discord.ui.TextInput(
            label="Typ wiadomości (dm / channel)",
            placeholder="Wpisz: dm albo channel",
            required=True,
            max_length=10
        )

        self.message_content = discord.ui.TextInput(
            label="Treść wiadomości",
            style=discord.TextStyle.paragraph,
            placeholder="Wpisz treść wiadomości...",
            required=True,
            max_length=2000
        )

        self.add_item(self.message_type)
        self.add_item(self.message_content)

    async def on_submit(self, interaction: discord.Interaction):
        typ = self.message_type.value.strip().lower()
        content = self.message_content.value

        if typ == "dm":
            try:
                await self.interaction.user.send(content)
                await interaction.response.send_message("✅ Wiadomość wysłana prywatnie!", ephemeral=True)
            except discord.Forbidden:
                await interaction.response.send_message("❌ Nie mogę wysłać wiadomości prywatnej.", ephemeral=True)
        elif typ == "channel":
            await interaction.response.send_message("📢 Wybierz kanał:", ephemeral=True, view=ChannelSelectView(content))
        else:
            await interaction.response.send_message("❗ Wpisz `dm` lub `channel` jako typ.", ephemeral=True)

class ChannelSelect(discord.ui.Select):
    def __init__(self, content):
        self.content = content
        options = [
            discord.SelectOption(label=channel.name, value=str(channel.id))
            for channel in bot.get_all_channels()
            if isinstance(channel, discord.TextChannel)
        ]
        super().__init__(placeholder="Wybierz kanał", options=options)

    async def callback(self, interaction: discord.Interaction):
        channel_id = int(self.values[0])
        channel = bot.get_channel(channel_id)
        if channel:
            await channel.send(self.content)
            await interaction.response.send_message(f"✅ Wiadomość wysłana na **{channel.name}**", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Nie znaleziono kanału.", ephemeral=True)

class ChannelSelectView(discord.ui.View):
    def __init__(self, content):
        super().__init__()
        self.add_item(ChannelSelect(content))

@bot.tree.command(name="wiadomosc", description="Wyślij wiadomość przez bota")
async def wiadomosc(interaction: discord.Interaction):
    await interaction.response.send_modal(MessageModal(interaction))

# --- Flask keep-alive ---
app = Flask('')

@app.route('/')
def home():
    return "Bot działa!"

def run():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    Thread(target=run).start()

# --- Komenda /chatp: Wyślij wiadomość prywatną do wskazanego użytkownika ---
@bot.tree.command(name="chatp", description="Wyślij prywatną wiadomość do wskazanego użytkownika")
@app_commands.describe(user="Użytkownik, do którego chcesz wysłać wiadomość")
async def chatp(interaction: discord.Interaction, user: discord.User):
    await interaction.response.send_modal(ChatpModal(user))


class ChatpModal(discord.ui.Modal, title="Wpisz treść wiadomości"):
    def __init__(self, target_user: discord.User):
        super().__init__()
        self.target_user = target_user

        self.message_content = discord.ui.TextInput(
            label="Treść wiadomości",
            style=discord.TextStyle.paragraph,
            placeholder="Wpisz wiadomość, którą chcesz wysłać...",
            required=True,
            max_length=2000
        )
        self.add_item(self.message_content)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            embed = Embed(
                title="💬 Otrzymałeś wiadomość od administracji",
                description=self.message_content.value,
                color=discord.Color.blurple(),
                timestamp=datetime.utcnow()
            )
            embed.set_footer(
                text=f"Nadawca: {interaction.user}",
                icon_url=interaction.user.avatar.url if interaction.user.avatar else None
            )
            await self.target_user.send(embed=embed)
            await interaction.response.send_message(f"✅ Wiadomość została wysłana do {self.target_user.mention}.", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message(
                "❌ Nie mogę wysłać wiadomości — użytkownik ma wyłączone wiadomości prywatne.",
                ephemeral=True
            )

# --- Start bota ---
@bot.event
async def on_ready():
    print(f'✅ Zalogowano jako {bot.user}')
    try:
        synced = await bot.tree.sync()
        print(f"✅ Zsynchronizowano {len(synced)} komend slash.")
    except Exception as e:
        print(f"❌ Błąd synchronizacji: {e}")

keep_alive()
TOKEN = os.getenv("DISCORD_TOKEN")
bot.run(TOKEN)
