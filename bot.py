import json
import os
import threading
from flask import Flask
import discord
from discord import app_commands
from discord.ext import commands

# ------------------- 1. 建立 Web Server (供 Render 健康檢查與防止休眠) -------------------
app = Flask('')

@app.route('/')
def home():
    return "Bot is alive and running!"

def run_web():
    port = int(os.environ.get("PORT", 8080))
    # 關閉 Flask 的偵錯日誌輸出，讓 Console 更乾淨
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = threading.Thread(target=run_web)
    t.daemon = True
    t.start()

# ------------------- 2. 載入資料與 Bot 初始化 -------------------
with open("data.json", "r", encoding="utf-8") as f:
    GAME_DATA = json.load(f)

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

# ------------------- 3. 報名流程 View 與 Modal -------------------

# 第三步：輸入被報名者名字 (Modal)
class CustomNameModal(discord.ui.Modal, title="輸入被報名者的名字"):
    def __init__(self, parent_view, selected_job: str, selected_card: str):
        super().__init__()
        self.parent_view = parent_view
        self.selected_job = selected_job
        self.selected_card = selected_card

        self.player_name_input = discord.ui.TextInput(
            label="玩家 ID / 角色名",
            placeholder="請輸入要報名的玩家暱稱或分身名",
            required=True,
            max_length=32
        )
        self.add_item(self.player_name_input)

    async def on_submit(self, interaction: discord.Interaction):
        # 先向 Discord 告知收到了，避免 Modal 卡住跳出「出問題了」
        await interaction.response.defer(ephemeral=True)

        player_name = self.player_name_input.value.strip()
        entry_id = f"{interaction.user.id}_{player_name}"

        # 紀錄報名資料
        self.parent_view.participants[entry_id] = {
            "name": f"{player_name} (由 {interaction.user.display_name} 幫報)",
            "job": self.selected_job,
            "card": self.selected_card,
            "submitter_id": interaction.user.id
        }

        # 更新原始副本招募 Embed 訊息
        if self.parent_view.message:
            await self.parent_view.message.edit(embed=self.parent_view.generate_embed())

        # 用 followup 發送成功訊息並自動關閉 Modal
        await interaction.followup.send(
            f"✅ 成功幫 **{player_name}** 報名！\n職業：**{self.selected_job}** | 同伴卡片：**{self.selected_card}**",
            ephemeral=True
        )
# 第二步：選擇卡片
class CardSelectView(discord.ui.View):
    def __init__(self, parent_view, selected_job: str, target_type: str):
        super().__init__(timeout=60)
        self.parent_view = parent_view
        self.selected_job = selected_job
        self.target_type = target_type

        options = [
            discord.SelectOption(label=card, value=card) 
            for card in GAME_DATA["partner_cards"]
        ]
        select = discord.ui.Select(
            placeholder="請選擇同伴卡片...",
            options=options
        )
        select.callback = self.card_callback
        self.add_item(select)

    async def card_callback(self, interaction: discord.Interaction):
        selected_card = interaction.data["values"][0]
        user = interaction.user

        if self.target_type == "other":
            await interaction.response.send_modal(
                CustomNameModal(
                    parent_view=self.parent_view,
                    selected_job=self.selected_job,
                    selected_card=selected_card
                )
            )
        else:
            entry_id = str(user.id)
            self.parent_view.participants[entry_id] = {
                "name": user.display_name,
                "job": self.selected_job,
                "card": selected_card,
                "submitter_id": user.id
            }

            if self.parent_view.message:
                await self.parent_view.message.edit(embed=self.parent_view.generate_embed())

            await interaction.response.edit_message(
                content=f"✅ 報名成功！職業：**{self.selected_job}** | 同伴卡片：**{selected_card}**",
                view=None
            )

# 第一步：選擇職業
class JobSelectView(discord.ui.View):
    def __init__(self, parent_view, target_type: str):
        super().__init__(timeout=60)
        self.parent_view = parent_view
        self.target_type = target_type

        options = [
            discord.SelectOption(label=job, value=job) 
            for job in GAME_DATA["jobs"]
        ]
        select = discord.ui.Select(
            placeholder="請選擇職業...",
            options=options
        )
        select.callback = self.job_callback
        self.add_item(select)

    async def job_callback(self, interaction: discord.Interaction):
        selected_job = interaction.data["values"][0]
        card_view = CardSelectView(
            parent_view=self.parent_view, 
            selected_job=selected_job, 
            target_type=self.target_type
        )
        await interaction.response.edit_message(
            content=f"已選擇職業：**{selected_job}**！請接著選擇同伴卡片：",
            view=card_view
        )

# 選擇報名對象：為自己 / 幫朋友
class TargetTypeView(discord.ui.View):
    def __init__(self, parent_view):
        super().__init__(timeout=60)
        self.parent_view = parent_view

    @discord.ui.button(label="🙋 為自己報名", style=discord.ButtonStyle.primary)
    async def self_register(self, interaction: discord.Interaction, button: discord.ui.Button):
        job_view = JobSelectView(parent_view=self.parent_view, target_type="self")
        await interaction.response.edit_message(content="請選擇你的職業：", view=job_view)

    @discord.ui.button(label="👥 幫朋友 / 分身報名", style=discord.ButtonStyle.secondary)
    async def other_register(self, interaction: discord.Interaction, button: discord.ui.Button):
        job_view = JobSelectView(parent_view=self.parent_view, target_type="other")
        await interaction.response.edit_message(content="請選擇該玩家的職業：", view=job_view)

# 招募主面板
class RegistrationView(discord.ui.View):
    def __init__(self, raid_name: str, time_str: str, max_players: int, host: discord.Member):
        super().__init__(timeout=None)
        self.raid_name = raid_name
        self.time_str = time_str
        self.max_players = max_players
        self.host = host
        self.participants = {}
        self.message = None

    def generate_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title=f"⚔️ 副本招募：{self.raid_name}",
            color=discord.Color.blue()
        )
        embed.add_field(name="👑 發起人", value=self.host.mention, inline=True)
        embed.add_field(name="⏰ 出發時間", value=self.time_str, inline=True)
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

        self.message = interaction.message
        target_view = TargetTypeView(parent_view=self)
        await interaction.response.send_message("請選擇報名類型：", view=target_view, ephemeral=True)

    @discord.ui.button(label="取消報名", style=discord.ButtonStyle.secondary, custom_id="leave_btn")
    async def leave_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = interaction.user.id
        user_entries = [k for k, v in self.participants.items() if v.get("submitter_id") == user_id]

        if not user_entries:
            await interaction.response.send_message("⚠️ 你目前沒有相關的報名紀錄。", ephemeral=True)
            return

        if len(user_entries) == 1:
            del self.participants[user_entries[0]]
            await interaction.message.edit(embed=self.generate_embed())
            await interaction.response.send_message("✅ 已成功取消報名。", ephemeral=True)
        else:
            del self.participants[user_entries[-1]]
            await interaction.message.edit(embed=self.generate_embed())
            await interaction.response.send_message("✅ 已取消你最近新增的一筆報名。", ephemeral=True)

    @discord.ui.button(label="解散副本", style=discord.ButtonStyle.red, custom_id="cancel_raid_btn")
    async def cancel_raid(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.host.id and not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ 只有發起人或管理員可以解散副本！", ephemeral=True)
            return

        await interaction.message.delete()
        await interaction.response.send_message(f"💥 副本 **{self.raid_name}** 已被發起人解散。", ephemeral=False)

# ------------------- 4. 斜線指令與啟動 -------------------
@bot.tree.command(name="create_raid", description="發起一個副本報名")
@app_commands.choices(raid=[app_commands.Choice(name=name, value=name) for name in GAME_DATA["raids"].keys()])
async def create_raid(interaction: discord.Interaction, raid: app_commands.Choice[str], time: str):
    raid_name = raid.value
    max_players = GAME_DATA["raids"][raid_name]["max_players"]

    view = RegistrationView(
        raid_name=raid_name, 
        time_str=time, 
        max_players=max_players, 
        host=interaction.user
    )
    embed = view.generate_embed()

    await interaction.response.send_message(embed=embed, view=view)

@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"🤖 機器人 {bot.user} 已上線並同步斜線指令！")

# 啟動 Web 伺服器
keep_alive()

# 啟動 Discord Bot
token = os.getenv("DISCORD_TOKEN")
bot.run(token)