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
    return "Bot is running!"

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
    await interaction.response.defer(thinking=True)
    target_ip = ip_address.strip().replace(" ", "")
    base_url = "http://ip-api.com"
    path = f"/json/{target_ip}"
    params = {"lang": "ja"}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{base_url}{path}", params=params) as response:
                if response.status != 200:
                    await interaction.followup.send("⚠️ 外部データサーバーとの通信に失敗しました。")
                    return
                data = await response.json()

        if data.get("status") == "fail":
            await interaction.followup.send(f"⚠️ 情報を取得できませんでした。\n理由: {data.get('message')}")
            return

        embed = discord.Embed(title=f"🌐 IPアドレス検索結果: {target_ip}", color=discord.Color.blue())
        embed.add_field(name="🌍 国", value=data.get("country", "不明"), inline=True)
        embed.add_field(name="🏙️ 都道府県/州", value=data.get("regionName", "不明"), inline=True)
        embed.add_field(name="📍 都市", value=data.get("city", "不明"), inline=True)
        embed.add_field(name="🏢 プロバイダ (ISP)", value=data.get("isp", "不明"), inline=False)
        await interaction.followup.send(embed=embed)
    except Exception as e:
        await interaction.followup.send(f"⚠️ エラーが発生しました: {e}")

# --- 4. 🚀【超安定版に強化】郵便番号検索コマンド ---
@bot.tree.command(name="zip", description="日本の郵便番号から住所を検索します（ハイフンなしでもOK）")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.describe(zip_code="検索したい7桁の郵便番号（例: 100-0005）")
async def lookup_zip(interaction: discord.Interaction, zip_code: str):
    await interaction.response.defer(thinking=True)

    # 入力された文字からハイフンや空白を消去
    clean_zip = zip_code.strip().replace("-", "").replace(" ", "")

    if not clean_zip.isdigit() or len(clean_zip) != 7:
        await interaction.followup.send("⚠️ 郵便番号は7桁の数字で入力してください。（例: 100-0005）")
        return

    # 💡 非常に安定している大手の郵便番号検索APIサービス（postcode-jp）に切り替え
    base_url = "https://postcode-jp.com"
    path = f"/v1/postcodes/{clean_zip}"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{base_url}{path}") as response:
                # 存在しない郵便番号などの場合は404が返ってくるためチェック
                if response.status == 404:
                    await interaction.followup.send("⚠️ 該当する住所が見つかりませんでした。郵便番号が正しいか確認してください。")
                    return
                elif response.status != 200:
                    await interaction.followup.send(f"⚠️ 郵便番号サーバーが混み合っています。少し時間を置いて再度お試しください。(Status: {response.status})")
                    return
                
                data = await response.json()

        # データの解析
        if not data or "data" not in data or len(data["data"]) == 0:
            await interaction.followup.send("⚠️ 該当する住所が見つかりませんでした。")
            return

        # 最初の該当住所を抽出
        address_info = data["data"][0]
        
        # 住所の各パーツ（都道府県、市区町村、町域）を取得
        pref = address_info.get("pref", "")
        city = address_info.get("city", "")
        town = address_info.get("town", "")
        full_address = f"{pref}{city}{town}"

        # カナ住所の取得
        pref_kana = address_info.get("pref_kana", "")
        city_kana = address_info.get("city_kana", "")
        town_kana = address_info.get("town_kana", "")
        kana_address = f"{pref_kana}{city_kana}{town_kana}"

        # 綺麗なカード（Embed）を作成
        embed = discord.Embed(
            title=f"📮 郵便番号検索結果: 〒{clean_zip[:3]}-{clean_zip[3:]}",
            color=discord.Color.green()
        )
        embed.add_field(name="📍 住所", value=full_address if full_address else "不明", inline=False)
        embed.add_field(name="📝 フリガナ", value=kana_address if kana_address else "不明", inline=False)
        
        await interaction.followup.send(embed=embed)

    except Exception as e:
        print(f"郵便番号検索エラー: {e}")
        await interaction.followup.send(f"⚠️ エラーが発生しました: {e}")

# --- 5. 起動処理 ---
if __name__ == "__main__":
    web_thread = threading.Thread(target=run_web)
    web_thread.daemon = True
    web_thread.start()

    token = os.environ.get("DISCORD_TOKEN")
    bot.run(token)
