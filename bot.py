import discord
from discord import app_commands
from discord.ext import commands, tasks

import asyncpg
from mcstatus import JavaServer

import json

from dotenv import load_dotenv
from os import getenv

load_dotenv()
TOKEN = getenv('TOKEN')
HOST = getenv('DB_HOST')
PORT = getenv('DB_PORT')
DATABASE = getenv('DB_DATABASE')
USER = getenv('DB_USER')
PASSWORD = getenv('DB_PASSWORD')

bot: commands.Bot = commands.Bot(command_prefix='sp!', intents=discord.Intents.all())
bot.config: dict = {}
bot.server_status = {}
bot.players = {}

async def get_db():
    bot.pool = await asyncpg.create_pool(host=HOST, port=PORT, database=DATABASE, user=USER, password=PASSWORD)

    async with bot.pool.acquire() as con:
        result = await con.fetchrow('SELECT version()')
        db_version = result[0]
        print(f'Database version: {db_version}')

async def load_config():
    async with bot.pool.acquire() as con:
        result = await con.fetchrow('SELECT * FROM config')
        bot.config = dict(result) if result else {'channel_id': None, 'alert_channel_id': None, 'message_id': None, 'ips': "{}", 'message': None, 'show_ip': False}
        bot.config['ips'] = json.loads(bot.config['ips'])

async def save_config():
    async with bot.pool.acquire() as con:
        await con.execute('DELETE FROM config')
        await con.execute('INSERT INTO config(channel_id, message_id, ips, message, alert_channel_id, show_ip) VALUES($1, $2, $3, $4, $5, $6)', bot.config['channel_id'], bot.config['message_id'], json.dumps(bot.config['ips']), bot.config['message'], bot.config['alert_channel_id'], bot.config['show_ip'])

def get_status_embed():
    embed = discord.Embed(color=discord.Color.green())
    embed.set_author(name="Server Status", icon_url=bot.user.avatar.url)
    if bot.config['show_ip']:
        for ip, name in bot.config['ips'].items():
            embed.add_field(name=f"{name} ({ip})", value=f"🟢 Online {bot.players[ip]}" if bot.server_status[ip] else "🔴 Offline", inline=False)
    else:
        for ip, name in bot.config['ips'].items():
            embed.add_field(name=f"{name}", value=f"🟢 Online {bot.players[ip]}" if bot.server_status[ip] else "🔴 Offline", inline=False)
    return embed

async def update_message():
    if bot.config['channel_id'] and bot.config['message_id']:
        channel = bot.get_channel(bot.config['channel_id'])
        message = await channel.fetch_message(bot.config['message_id'])
        await message.edit(embed=get_status_embed())

async def send_alert(server: str):
    if bot.config['alert_channel_id']:
        channel = bot.get_channel(bot.config['alert_channel_id'])
        await channel.send(f"{bot.config['message'].format(server=server)}")

@tasks.loop(minutes=10)
async def check_servers():
    for ip in bot.config["ips"].keys():
        try:
            server: JavaServer = await JavaServer.async_lookup(ip)
            status = await server.async_status()
            bot.players[ip] = f"{status.players.online}/{status.players.max}"
            bot.server_status[ip] = True
        except ConnectionRefusedError as e:
            if bot.server_status[ip]:
                bot.server_status[ip] = False
            else:
                await send_alert(bot.config['ips'][ip])
    await update_message()

@bot.event
async def on_ready():
    print(f'{bot.user.name} has connected to Discord!')
    await get_db()
    await load_config()
    for ip in bot.config['ips'].keys():
        bot.server_status[ip] = True
        bot.players[ip] = "?/?"
    check_servers.start()

@bot.command()
@commands.is_owner()
async def sync(ctx):
    await ctx.message.delete()
    res = await bot.tree.sync()
    await ctx.send(res, delete_after=5)

@bot.tree.command(description="Get the status of the current servers")
async def status(interaction: discord.Interaction):
    embed = discord.Embed(color=discord.Color.green())
    embed.set_author(name="Server Status", icon_url=bot.user.avatar.url)
    for ip, name in bot.config['ips'].items():
        embed.add_field(name=f"{name} ({ip})", value="🟢 Online" if bot.server_status[ip] else "🔴 Offline", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(description="Add a server to the list")
async def addserver(interaction: discord.Interaction, ip: str, name: str):
    bot.config['ips'][ip] = name
    bot.server_status[ip] = False
    bot.players[ip] = "?/?"
    await update_message()
    await save_config()
    await interaction.response.send_message(f"Added {name} ({ip}) to the list", ephemeral=True)

@bot.tree.command(description="Remove a server from the list")
async def removeserver(interaction: discord.Interaction, ip: str):
    name = bot.config['ips'][ip]
    del bot.config['ips'][ip]
    bot.server_status.pop(ip)
    bot.players.pop(ip)
    await update_message()
    await save_config()
    await interaction.response.send_message(f"Removed {name} ({ip}) from the list", ephemeral=True)

@removeserver.autocomplete("ip")
async def autocomplete_ips(interaction: discord.Interaction, current: str):
    return [app_commands.Choice(name=name, value=ip) for ip, name in bot.config['ips'].items() if ip.startswith(current)]

@bot.tree.command(description="Set the channel to send the status message in")
async def setchannel(interaction: discord.Interaction, channel: discord.TextChannel):
    if bot.config['channel_id'] and bot.config['message_id']:
        try:
            old_channel = bot.get_channel(bot.config['channel_id'])
            old_message = await old_channel.fetch_message(bot.config['message_id'])
            await old_message.delete()
        except discord.NotFound:
            pass
    bot.config['channel_id'] = channel.id
    m = await channel.send(embed=get_status_embed())
    bot.config['message_id'] = m.id
    await save_config()
    await interaction.response.send_message(f"Set the channel to {channel.mention}", ephemeral=True)

@bot.tree.command(description="Set the alert when a server is offline")
async def setalert(interaction: discord.Interaction, channel: discord.TextChannel, message: str):
    bot.config['alert_channel_id'] = channel.id
    bot.config['message'] = message
    await save_config()
    await interaction.response.send_message(f"Set the alert to {channel.mention} with message {message}", ephemeral=True)

@bot.tree.command(description="Toggle showing the IP in the status message")
async def toggleip(interaction: discord.Interaction):
    bot.config['show_ip'] = not bot.config['show_ip']
    await update_message()
    await save_config()
    await interaction.response.send_message(f"Set show_ip to {bot.config['show_ip']}", ephemeral=True)

bot.run(TOKEN)