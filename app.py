import os
import threading
import aiohttp
import discord
from discord.ext import commands
from discord import app_commands
from flask import Flask

# --- 1. Flask（簡易Webサーバー）の設定 ---
app = Flask(__name__)

@app.route('/')
def home():
    return "IP Lookup Bot is running!"

def run_web():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

# --- 2. Discord Botの設定 ---
intents = discord.Intents.default()
# ユーザーインストール型（外部アプリ）としてどこでも動くように設定
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    try:
        synced = await bot.tree.sync()
        print(f"Successfully synced {len(synced)} command(s).")
    except Exception as e:
        print(f"Failed to sync commands: {e}")

# --- 3. IPアドレス検索コマンド ---
@bot.tree.command(name="ip", description="指定されたIPアドレスの国や都市、プロバイダ情報を検索します")
@app_commands.allowed_installs(guilds=True, users=True)  # 他人のサーバーやDMでも使用可能に
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.describe(ip_address="検索したいIPアドレス（例: 8.8.8.8）")
async def lookup_ip(interaction: discord.Interaction, ip_address: str):
    # 検索に少し時間がかかる場合があるため、応答を保留
    await interaction.response.defer(thinking=True)

    # 空白を削除
    target_ip = ip_address.strip()

    # ip-api.com の無料APIを利用して情報を取得
    api_url = f"http://ip-api.com{target_ip}?lang=ja"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(api_url) as response:
                if response.status != 200:
                    await interaction.followup.send("⚠️ 外部データサーバーとの通信に失敗しました。")
                    return
                
                data = await response.json()

        # APIのステータスが失敗(fail)の場合
        if data.get("status") == "fail":
            await interaction.followup.send(f"⚠️ 情報を取得できませんでした。IPアドレスが正しいか確認してください。\n理由: {data.get('message')}")
            return

        # 💡 取得したデータを綺麗に並べて埋め込み（Embed）メッセージを作成
        embed = discord.Embed(
            title=f"🌐 IPアドレス検索結果: {target_ip}",
            color=discord.Color.blue()
        )
        embed.add_field(name="🌍 国 (Country)", value=data.get("country", "不明"), inline=True)
        embed.add_field(name="🏙️ 都道府県/州 (Region)", value=data.get("regionName", "不明"), inline=True)
        embed.add_field(name="📍 都市 (City)", value=data.get("city", "不明"), inline=True)
        embed.add_field(name="🏢 プロバイダ (ISP)", value=data.get("isp", "不明"), inline=False)
        embed.add_field(name="📮 郵便番号", value=data.get("zip", "不明"), inline=True)
        embed.add_field(name="⏰ タイムゾーン", value=data.get("timezone", "不明"), inline=True)

        # 検索結果を送信
        await interaction.followup.send(embed=embed)

    except Exception as e:
        print(f"IP検索エラー: {e}")
        await interaction.followup.send(f"⚠️ エラーが発生しました: {e}")

# --- 4. 起動処理 ---
if __name__ == "__main__":
    web_thread = threading.Thread(target=run_web)
    web_thread.daemon = True
    web_thread.start()

    token = os.environ.get("DISCORD_TOKEN")
    bot.run(token)
