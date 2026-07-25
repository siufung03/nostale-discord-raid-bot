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

# ------------------- 第二步：選擇卡片的下拉選單 -------------------
class CardSelectView(discord.ui.View):
    def __init__(self, parent_view, selected_job: str):
        super().__init__(timeout=60)
        self.parent_view = parent_view
        self.selected_job = selected_job

        # 動態建立卡片下拉選單
        options = [
            discord.SelectOption(label=card, value=card) 
            for card in GAME_DATA["partner_cards"]
        ]
        select = discord.ui.Select(
            placeholder="請選擇你的同伴卡片...",
            options=options
        )
        select.callback = self.card_callback
        self.add_item(select)

    async def card_callback(self, interaction: discord.Interaction):
        selected_card = interaction.data["values"][0]
        user = interaction.user

        # 寫入報名資料
        self.parent_view.participants[user.id] = {
            "name": user.display_name,
            "job": self.selected_job,
            "card": selected_card
        }

        # 更新原本的報名 Embed 面板
        time_str = interaction.message.embeds[0].fields[1].value if interaction.message.embeds else ""
        # 尋找原始招募訊息並更新
        await self.parent_view.message.edit(embed=self.parent_view.generate_embed(time_str))

        await interaction.response.edit_message(
            content=f"✅ 報名成功！職業：**{self.selected_job}** | 同伴卡片：**{selected_card}**",
            view=None
        )

# ------------------- 第一步：選擇職業的下拉選單 -------------------
class JobSelectView(discord.ui.View):
    def __init__(self, parent_view):
        super().__init__(timeout=60)
        self.parent_view = parent_view

        options = [
            discord.SelectOption(label=job, value=job) 
            for job in GAME_DATA["jobs"]
        ]
        select = discord.ui.Select(
            placeholder="請選擇你的職業...",
            options=options
        )
        select.callback = self.job_callback
        self.add_item(select)

    async def job_callback(self, interaction: discord.Interaction):
        selected_job = interaction.data["values"][0]
        # 選完職業後，跳出下一個選單選卡片
        card_view = CardSelectView(parent_view=self.parent_view, selected_job=selected_job)
        await interaction.response.edit_message(
            content=f"已選擇職業：**{selected_job}**！請接著選擇你的同伴卡片：",
            view=card_view
        )

# ------------------- 招募主面板 View -------------------
class RegistrationView(discord.ui.View):
    def __init__(self, raid_name: str, max_players: int, host: discord.Member):
        super().__init__(timeout=None)
        self.raid_name = raid_name
        self.max_players = max_players
        self.host = host
        self.participants = {}
        self.message = None

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

        # 記住原始訊息以利更新
        self.message = interaction.message

        # 發送私人職業選單 (ephemeral=True，只有點擊者看得到)
        job_view = JobSelectView(parent_view=self)
        await interaction.response.send_message("請選擇你的職業：", view=job_view, ephemeral=True)

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

token = os.getenv("DISCORD_TOKEN")
bot.run(token)