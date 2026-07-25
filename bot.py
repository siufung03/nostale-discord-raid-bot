import os
import json
import threading
from flask import Flask
import discord
from discord import app_commands
from discord.ext import commands

# 建立極簡 Web 伺服器供 Render 檢測
app = Flask('')
@app.route('/')
def home():
    return "Bot is alive!"

def run_web():
    # Render 會自動提供 PORT 環境變數
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

# 在背景線程啟動 Web 伺服器
threading.Thread(target=run_web, daemon=True).start()

# 載入資料檔
with open("data.json", "r", encoding="utf-8") as f:
    GAME_DATA = json.load(f)

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

# ------------------- 報名選擇面板 -------------------
class RegistrationView(discord.ui.View):
    def __init__(self, raid_name: str, max_players: int, host: discord.Member):
        super().__init__(timeout=None)
        self.raid_name = raid_name
        self.max_players = max_players
        self.host = host
        self.participants = {}

    def generate_embed(self, time_str: str) -> discord.Embed:
        embed = discord.Embed(
            title=f"⚔️ 副本招募：{self.raid_name}",
            color=discord.Color.blue()
        )
        embed.add_field(name="👑 發起人", value=self.host.mention, inline=True)
        embed.add_field(name="⏰ 出發時間", value=time_str, inline=True)
        embed.add_field(name="👥 人數限制", value=f"{len(self.participants)} / {self.max_players}", inline=True)

        player_list = ""
        if not self.participants:
            player_list = "暫無人報名"
        else:
            for idx, p in enumerate(self.participants.values(), 1):
                player_list += f"{idx}. {p['name']} - **{p['job']}** (卡片: {p['card']})\n"

        embed.add_field(name="📋 已報名玩家", value=player_list, inline=False)
        return embed

    @discord.ui.button(label="報名", style=discord.ButtonStyle.green, custom_id="join_btn")
    async def join_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if len(self.participants) >= self.max_players:
            await interaction.response.send_message("❌ 該副本人數已滿！", ephemeral=True)
            return
        await interaction.response.send_modal(SelectionModal(parent_view=self))

    @discord.ui.button(label="取消報名", style=discord.ButtonStyle.secondary, custom_id="leave_btn")
    async def leave_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = interaction.user.id
        if user_id in self.participants:
            del self.participants[user_id]
            time_str = interaction.message.embeds[0].fields[1].value
            await interaction.message.edit(embed=self.generate_embed(time_str))
            await interaction.response.send_message("✅ 已取消報名。", ephemeral=True)
        else:
            await interaction.response.send_message("⚠️ 你本來就沒有報名這個副本。", ephemeral=True)

    @discord.ui.button(label="解散副本", style=discord.ButtonStyle.red, custom_id="cancel_raid_btn")
    async def cancel_raid(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.host.id and not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ 只有發起人或管理員可以解散副本！", ephemeral=True)
            return

        await interaction.message.delete()
        await interaction.response.send_message(f"💥 副本 **{self.raid_name}** 已被發起人解散。", ephemeral=False)

# ------------------- 報名彈窗 -------------------
class SelectionModal(discord.ui.Modal, title="選擇你的職業與同伴卡片"):
    def __init__(self, parent_view: RegistrationView):
        super().__init__()
        self.parent_view = parent_view

        self.job_input = discord.ui.TextInput(
            label="請輸入你的職業",
            placeholder=f"可選: {', '.join(GAME_DATA['jobs'])}",
            required=True
        )
        self.card_input = discord.ui.TextInput(
            label="請輸入同伴卡片",
            placeholder=f"可選: {', '.join(GAME_DATA['partner_cards'])}",
            required=True
        )
        self.add_item(self.job_input)
        self.add_item(self.card_input)

    async def on_submit(self, interaction: discord.Interaction):
        user = interaction.user
        job = self.job_input.value.strip()
        card = self.card_input.value.strip()

        self.parent_view.participants[user.id] = {
            "name": user.display_name,
            "job": job,
            "card": card
        }

        time_str = interaction.message.embeds[0].fields[1].value
        await interaction.message.edit(embed=self.parent_view.generate_embed(time_str))
        await interaction.response.send_message(f"✅ 報名成功！職業: **{job}** | 卡片: **{card}**", ephemeral=True)

# ------------------- 斜線指令 -------------------
@bot.tree.command(name="create_raid", description="發起一個副本報名")
@app_commands.choices(raid=[app_commands.Choice(name=name, value=name) for name in GAME_DATA["raids"].keys()])
async def create_raid(interaction: discord.Interaction, raid: app_commands.Choice[str], time: str):
    raid_name = raid.value
    max_players = GAME_DATA["raids"][raid_name]["max_players"]

    view = RegistrationView(raid_name=raid_name, max_players=max_players, host=interaction.user)
    embed = view.generate_embed(time_str=time)

    await interaction.response.send_message(embed=embed, view=view)

@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"🤖 機器人 {bot.user} 已上線並同步斜線指令！")

# 從環境變數讀取 Token
token = os.getenv("DISCORD_TOKEN")
if not token:
    raise ValueError("找不到 DISCORD_TOKEN 環境變數！")

bot.run(token)