import json
import os
import threading
from flask import Flask
import discord
from discord import app_commands
from discord.ext import commands

# ------------------- 1. Web Server (供 Render 健康檢查與防止休眠) -------------------
app = Flask('')

@app.route('/')
def home():
    return "Bot is alive and running!"

def run_web():
    port = int(os.environ.get("PORT", 8080))
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

# ------------------- 3. 取消報名選單 (精準選擇取消哪一個) -------------------
class CancelSelectView(discord.ui.View):
    def __init__(self, parent_view, user_entries):
        super().__init__(timeout=60)
        self.parent_view = parent_view

        # user_entries: list of (entry_id, display_name)
        options = [
            discord.SelectOption(label=display_name, value=entry_id)
            for entry_id, display_name in user_entries
        ]
        select = discord.ui.Select(
            placeholder="請選擇你要取消的報名項目...",
            options=options
        )
        select.callback = self.cancel_callback
        self.add_item(select)

    async def cancel_callback(self, interaction: discord.Interaction):
        entry_id_to_remove = interaction.data["values"][0]

        if entry_id_to_remove in self.parent_view.participants:
            removed_info = self.parent_view.participants.pop(entry_id_to_remove)
            
            # 更新主 Embed
            if self.parent_view.message:
                await self.parent_view.message.edit(
                    embed=self.parent_view.generate_embed(),
                    view=self.parent_view.get_dynamic_view(interaction.user)
                )

            await interaction.response.edit_message(
                content=f"✅ 已成功取消 **{removed_info['name']}** 的報名。",
                view=None
            )
        else:
            await interaction.response.edit_message(content="⚠️ 該報名紀錄已被刪除或不存在。", view=None)

# ------------------- 4. 輸入幫報名者名字 (Modal) -------------------
class CustomNameModal(discord.ui.Modal, title="輸入被報名者的名字"):
    def __init__(self, parent_view, selected_job: str):
        super().__init__()
        self.parent_view = parent_view
        self.selected_job = selected_job

        self.player_name_input = discord.ui.TextInput(
            label="玩家 ID / 角色名",
            placeholder="請輸入要報名的玩家暱稱或分身名",
            required=True,
            max_length=32
        )
        self.add_item(self.player_name_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        player_name = self.player_name_input.value.strip()
        entry_id = f"{interaction.user.id}_{player_name}"

        self.parent_view.participants[entry_id] = {
            "name": f"{player_name} (由 {interaction.user.display_name} 幫報)",
            "job": self.selected_job,
            "submitter_id": interaction.user.id
        }

        if self.parent_view.message:
            await self.parent_view.message.edit(
                embed=self.parent_view.generate_embed(),
                view=self.parent_view.get_dynamic_view(interaction.user)
            )

        await interaction.followup.send(
            f"✅ 成功幫 **{player_name}** 報名！\n職業：**{self.selected_job}**",
            ephemeral=True
        )

# ------------------- 5. 選擇職業 -------------------
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
        user = interaction.user

        if self.target_type == "other":
            # 幫別人報名：彈出輸入視窗
            await interaction.response.send_modal(
                CustomNameModal(parent_view=self.parent_view, selected_job=selected_job)
            )
        else:
            # 為自己報名：直接完成
            entry_id = str(user.id)
            self.parent_view.participants[entry_id] = {
                "name": user.display_name,
                "job": selected_job,
                "submitter_id": user.id
            }

            if self.parent_view.message:
                await self.parent_view.message.edit(
                    embed=self.parent_view.generate_embed(),
                    view=self.parent_view.get_dynamic_view(interaction.user)
                )

            await interaction.response.edit_message(
                content=f"✅ 報名成功！職業：**{selected_job}**",
                view=None
            )

# ------------------- 6. 選擇報名類型 -------------------
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

# ------------------- 7. 招募主面板 -------------------
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
                player_list += f"{idx}. {p['name']} - **{p['job']}**\n"

        embed.add_field(name="📋 已報名玩家", value=player_list, inline=False)
        return embed

    # 根據觀看的使用者權限，生成對應的按鈕 View (解決解散按鈕可見度問題)
    def get_dynamic_view(self, user: discord.User | discord.Member) -> discord.ui.View:
        view = discord.ui.View(timeout=None)
        
        # 報名按鈕
        join_btn = discord.ui.Button(label="報名", style=discord.ButtonStyle.green, custom_id="join_btn")
        join_btn.callback = self.join_button_callback
        view.add_item(join_btn)

        # 取消報名按鈕
        leave_btn = discord.ui.Button(label="取消報名", style=discord.ButtonStyle.secondary, custom_id="leave_btn")
        leave_btn.callback = self.leave_button_callback
        view.add_item(leave_btn)

        # 解散副本按鈕：僅限發起人或管理員可見
        is_host = user.id == self.host.id
        is_admin = getattr(user.guild_permissions, "administrator", False) if isinstance(user, discord.Member) else False

        if is_host or is_admin:
            cancel_btn = discord.ui.Button(label="解散副本", style=discord.ButtonStyle.red, custom_id="cancel_raid_btn")
            cancel_btn.callback = self.cancel_raid_callback
            view.add_item(cancel_btn)

        return view

    async def join_button_callback(self, interaction: discord.Interaction):
        if len(self.participants) >= self.max_players:
            await interaction.response.send_message("❌ 該副本人數已滿！", ephemeral=True)
            return

        self.message = interaction.message
        target_view = TargetTypeView(parent_view=self)
        await interaction.response.send_message("請選擇報名類型：", view=target_view, ephemeral=True)

    async def leave_button_callback(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        # 找到所有由該使用者提交的項目 (key, display_name)
        user_entries = [
            (k, v["name"]) for k, v in self.participants.items() 
            if v.get("submitter_id") == user_id
        ]

        if not user_entries:
            await interaction.response.send_message("⚠️ 你目前沒有相關的報名紀錄。", ephemeral=True)
            return

        # 如果只有一筆紀錄，直接取消
        if len(user_entries) == 1:
            entry_id, entry_name = user_entries[0]
            del self.participants[entry_id]
            
            if self.message:
                await self.message.edit(
                    embed=self.generate_embed(),
                    view=self.get_dynamic_view(interaction.user)
                )

            await interaction.response.send_message(f"✅ 已成功取消 **{entry_name}** 的報名。", ephemeral=True)
        else:
            # 如果有多筆紀錄，彈出下拉選單讓玩家選擇要取消哪一個
            cancel_view = CancelSelectView(parent_view=self, user_entries=user_entries)
            await interaction.response.send_message("請選擇要取消哪一個報名：", view=cancel_view, ephemeral=True)

    async def cancel_raid_callback(self, interaction: discord.Interaction):
        is_host = interaction.user.id == self.host.id
        is_admin = getattr(interaction.user.guild_permissions, "administrator", False) if isinstance(interaction.user, discord.Member) else False

        if not (is_host or is_admin):
            await interaction.response.send_message("❌ 只有發起人或管理員可以解散副本！", ephemeral=True)
            return

        await interaction.message.delete()
        await interaction.response.send_message(f"💥 副本 **{self.raid_name}** 已被發起人解散。", ephemeral=False)

# ------------------- 8. 斜線指令與啟動 -------------------
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

    # 使用 get_dynamic_view 確保發起人創建貼文時能看到解散按鈕
    await interaction.response.send_message(
        embed=embed, 
        view=view.get_dynamic_view(interaction.user)
    )

@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"🤖 機器人 {bot.user} 已上線並同步斜線指令！")

# 啟動 Web 伺服器
keep_alive()

# 啟動 Discord Bot
token = os.getenv("DISCORD_TOKEN")
bot.run(token)