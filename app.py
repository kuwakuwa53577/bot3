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
intents.message_content = True 
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
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.describe(ip_address="検索したいIPアドレス（例: 8.8.8.8）")
async def lookup_ip(interaction: discord.Interaction, ip_address: str):
    # 応答を保留
    await interaction.response.defer(thinking=True)

    # 入力された文字から空白や余計な文字を徹底的に掃除
    target_ip = ip_address.strip().replace(" ", "")

    # 💡【絶対にバグらない仕組みに修正】
    # 文字列の組み合わせ（f-string）をやめて、URLをパーツごとに安全に組み立てる方式に変更しました。
    base_url = "http://ip-api.com"
    path = f"/json/{target_ip}"
    params = {"lang": "ja"}

    try:
        async with aiohttp.ClientSession() as session:
            # base_url と path が自動的に正しいスラッシュで結合されます
            async with session.get(f"{base_url}{path}", params=params) as response:
                if response.status != 200:
                    await interaction.followup.send("⚠️ 外部データサーバーとの通信に失敗しました。")
                    return
                
                data = await response.json()

        # APIのステータスが失敗(fail)の場合
        if data.get("status") == "fail":
            await interaction.followup.send(f"⚠️ 情報を取得できませんでした。IPアドレスが正しいか確認してください。\n理由: {data.get('message')}")
            return

        # 取得したデータを埋め込み（Embed）メッセージにセット
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

        # 結果を送信
        await interaction.followup.send(embed=embed)

    except Exception as e:
        print(f"IP検索エラー: {e}")
        await interaction.followup.send(f"⚠️ エラーが発生しました。プログラムの更新がRenderに反映されているか確認してください。\n詳細: {e}")

# --- 4. 起動処理 ---
if __name__ == "__main__":
    web_thread = threading.Thread(target=run_web)
    web_thread.daemon = True
    web_thread.start()

    token = os.environ.get("DISCORD_TOKEN")
    bot.run(token)
