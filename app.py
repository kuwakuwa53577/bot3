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

# --- 3. IPアドレス検索コマンド（全22情報対応版） ---
@bot.tree.command(name="ip", description="指定されたIPアドレスのすべての情報（22種類）を検索します")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.describe(ip_address="検索したいIPアドレス（例: 8.8.8.8）")
async def lookup_ip(interaction: discord.Interaction, ip_address: str):
    await interaction.response.defer(thinking=True)
    target_ip = ip_address.strip().replace(" ", "")
    base_url = "http://ip-api.com"
    path = f"/json/{target_ip}"
    
    # 💡 全22情報を取得するために、すべてのフィールド（fields=66846719）を明示的に要求します
    params = {
        "lang": "ja",
        "fields": "66846719"
    }

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

        # 綺麗な青いカード（Embed）を作成
        embed = discord.Embed(
            title=f"🌐 IPアドレス詳細解析結果: {data.get('query', target_ip)}", 
            color=discord.Color.blue(),
            description="APIから取得できる全22情報を表示しています。"
        )

        # 🗺️ 1. 位置情報・住所グループ（10情報）
        embed.add_field(name="📍 住所・タイムゾーン", value=(
            f"**大陸:** {data.get('continent', '不明')} ({data.get('continentCode', '-')})\n"
            f"**国名:** {data.get('country', '不明')} ({data.get('countryCode', '-')})\n"
            f"**都道府県/州:** {data.get('regionName', '不明')} ({data.get('region', '-')})\n"
            f"**都市:** {data.get('city', '不明')}\n"
            f"**区・地域:** {data.get('district', 'なし・不明')}\n"
            f"**郵便番号:** 〒{data.get('zip', '不明')}\n"
            f"**タイムゾーン:** {data.get('timezone', '不明')}"
        ), inline=False)

        # 🌐 2. 座標・時間グループ（3情報）
        embed.add_field(name="🧭 地図・座標・時差", value=(
            f"**緯度 (Lat):** {data.get('lat', '不明')}\n"
            f"**経度 (Lon):** {data.get('lon', '不明')}\n"
            f"**UTC時差:** {data.get('offset', 0) / 3600:+g} 時間"
        ), inline=True)

        # 💴 3. 通貨（1情報）
        embed.add_field(name="💴 通貨", value=f"{data.get('currency', '不明')}", inline=True)

        # 🏢 4. 回線・プロバイダグループ（5情報）
        embed.add_field(name="🏢 通信会社・ネットワーク", value=(
            f"**プロバイダ (ISP):** {data.get('isp', '不明')}\n"
            f"**組織名 (Org):** {data.get('org', '不明')}\n"
            f"**AS番号:** {data.get('as', '不明')}\n"
            f"**AS名:** {data.get('asname', '不明')}"
        ), inline=False)

        # 🚨 5. 特殊判定グループ（3情報）
        # Trueなら 🔴、Falseなら 🟢 で直感的にわかりやすく表示します
        is_mobile = "🔴 モバイル回線 (4G/5G)" if data.get("mobile") else "🟢 固定回線など"
        is_proxy = "🔴 VPN / プロキシ検出" if data.get("proxy") else "🟢 通常の接続 (安全)"
        is_hosting = "🔴 サーバー（データセンター）" if data.get("hosting") else "🟢 通常の一般回線"

        embed.add_field(name="🚨 特殊な通信の判定", value=(
            f"**回線種別:** {is_mobile}\n"
            f"**匿名化ツール:** {is_proxy}\n"
            f"**ホスティング:** {is_hosting}"
        ), inline=False)

        await interaction.followup.send(embed=embed)
        
        await interaction.followup.send(embed=embed)
    except Exception as e:
        await interaction.followup.send(f"⚠️ エラーが発生しました: {e}")

# --- 4. 🚀【URL修正完了】郵便番号検索コマンド ---
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

    # 💡【修正ポイント】正しいデータ配信用のアドレス「api.」を追加しました
    base_url = "https://api.zipaddress.net"
    params = {"zipcode": clean_zip}

    try:
        async with aiohttp.ClientSession() as session:
            # 外部サーバーからデータを安全に読み込みます
            async with session.get(base_url, params=params) as response:
                if response.status != 200:
                    await interaction.followup.send(f"⚠️ 郵便番号サーバーとの通信に失敗しました。(Status: {response.status})")
                    return
                
                # エラー（Unexpected mimetype）を防ぐため、content_type=None を指定して確実にJSONとして解析します
                data = await response.json(content_type=None)

        # 💡 APIの仕様に合わせたステータスチェック
        if data.get("code") != 200 or not data.get("data"):
            await interaction.followup.send("⚠️ 該当する住所が見つかりませんでした。郵便番号が正しいか確認してください。")
            return

        address_info = data["data"]
        
        # APIが返してくれる結合済みの住所（fullAddress）を綺麗に抽出
        full_address = address_info.get("fullAddress", "")

        # 綺麗な緑色のカード（Embed）を作成
        embed = discord.Embed(
            title=f"📮 郵便番号検索結果: 〒{clean_zip[:3]}-{clean_zip[3:]}",
            color=discord.Color.green()
        )
        embed.add_field(name="📍 住所", value=full_address if full_address else "不明", inline=False)
        
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
