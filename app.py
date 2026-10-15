import os
import threading
import aiohttp
import discord
from discord.ext import commands
from discord import app_commands
from flask import Flask
import base64

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

# --- 3. IPアドレス検索コマンド（URLバグ完全修正版） ---
@bot.tree.command(name="ip", description="指定されたIPアドレスの国、都市、プロバイダ、住所情報を検索します")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.describe(ip_address="検索したいIPアドレス（例: 8.8.8.8）")
async def lookup_ip(interaction: discord.Interaction, ip_address: str):
    await interaction.response.defer(thinking=True)
    target_ip = ip_address.strip().replace(" ", "")
    
    # 💡 URLの末尾に正しくスラッシュを入れてIPをテキストとして結合
    ip_url = f"http://ip-api.com{target_ip}"
    ip_params = {"lang": "ja", "fields": "66846719"}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(ip_url, params=ip_params) as response:
                if response.status != 200:
                    await interaction.followup.send("⚠️ 外部データサーバーとの通信に失敗しました。")
                    return
                data = await response.json()

        if data.get("status") == "fail":
            await interaction.followup.send(f"⚠️ 情報を取得できませんでした。理由: {data.get('message')}")
            return

        zip_code = data.get("zip", "").strip().replace("-", "").replace(" ", "")
        full_address = "不明または日本国外"
        
        if zip_code and len(zip_code) == 7 and zip_code.isdigit():
            # 💡 正しい大手の郵便番号APIを使用
            zip_url = "https://ibsnet.co.jp"
            zip_params = {"zipcode": zip_code}
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(zip_url, params=zip_params) as z_resp:
                        if z_resp.status == 200:
                            z_data = await z_resp.json()
                            if z_data.get("status") == 200 and z_data.get("results"):
                                res = z_data["results"][0]
                                full_address = f"{res.get('address1', '')}{res.get('address2', '')}{res.get('address3', '')}"
            except Exception:
                full_address = "住所逆引き中にエラーが発生しました"

        embed = discord.Embed(title=f"🌐 IPアドレス詳細解析結果: {data.get('query', target_ip)}", color=discord.Color.blue())
        
        embed.add_field(name="📍 住所・タイムゾーン", value=(
            f"**自動逆引き住所:** {full_address}\n"
            f"**国名:** {data.get('country', '不明')} ({data.get('countryCode', '-')})\n"
            f"**都道府県/州:** {data.get('regionName', '不明')}\n"
            f"**都市/区:** {data.get('city', '不明')} / {data.get('district', 'なし')}\n"
            f"**郵便番号:** 〒{data.get('zip', '不明')}\n"
            f"**タイムゾーン:** {data.get('timezone', '不明')}"
        ), inline=False)

        embed.add_field(name="🧭 座標・時差・通貨", value=(
            f"**緯度/経度:** {data.get('lat', '-')}, {data.get('lon', '-')}\n"
            f"**UTC時差:** {data.get('offset', 0) / 3600:+g} 時間\n"
            f"**使用通貨:** {data.get('currency', '不明')}"
        ), inline=False)

        embed.add_field(name="🏢 通信会社・ネットワーク", value=(
            f"**プロバイダ (ISP):** {data.get('isp', '不明')}\n"
            f"**組織名 (Org):** {data.get('org', '不明')}\n"
            f"**AS番号/名:** {data.get('as', '不明')} / {data.get('asname', '不明')}"
        ), inline=False)

        is_mobile = "🔴 モバイル回線 (4G/5G)" if data.get("mobile") else "🟢 固定回線など"
        is_proxy = "🔴 VPN / プロキシ検出" if data.get("proxy") else "🟢 通常の接続"
        is_hosting = "🔴 サーバー（データセンター）" if data.get("hosting") else "🟢 通常の一般回線"
        embed.add_field(name="🚨 特殊回線判定", value=f"**回線種類:** {is_mobile}\n**匿名ツール:** {is_proxy}\n**ホスティング:** {is_hosting}", inline=False)

        await interaction.followup.send(embed=embed)
    except Exception as e:
        await interaction.followup.send(f"⚠️ エラーが発生しました: {e}")

# --- 4. 郵便番号検索コマンド（バグ完全修正版） ---
@bot.tree.command(name="zip", description="日本の郵便番号から住所を検索します")
@app_commands.describe(zip_code="検索したい7桁の郵便番号（例: 100-0005）")
async def lookup_zip(interaction: discord.Interaction, zip_code: str):
    await interaction.response.defer(thinking=True)
    clean_zip = zip_code.strip().replace("-", "").replace(" ", "")
    if not clean_zip.isdigit() or len(clean_zip) != 7:
        await interaction.followup.send("⚠️ 郵便番号は7桁の数字で入力してください。")
        return
        
    base_url = "https://ibsnet.co.jp"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(base_url, params={"zipcode": clean_zip}) as response:
                if response.status != 200:
                    await interaction.followup.send("⚠️ 郵便番号サーバーとの通信に失敗しました。")
                    return
                data = await response.json()
                
        if data.get("status") != 200 or not data.get("results"):
            await interaction.followup.send("⚠️ 該当する住所が見つかりませんでした。郵便番号が正しいか確認してください。")
            return
            
        res = data["results"][0]
        full_address = f"{res.get('address1', '')}{res.get('address2', '')}{res.get('address3', '')}"
        
        embed = discord.Embed(title=f"📮 郵便番号検索結果: 〒{clean_zip[:3]}-{clean_zip[3:]}", color=discord.Color.green())
        embed.add_field(name="📍 住所", value=full_address, inline=False)
        await interaction.followup.send(embed=embed)
    except Exception as e:
        await interaction.followup.send(f"⚠️ エラーが発生しました: {e}")

# --- 5. ドメイン検索コマンド（バグ完全修正版） ---
@bot.tree.command(name="domain", description="WEBサイトのドメイン情報を検索します")
@app_commands.describe(domain_name="検索したいドメイン（例: google.com）")
async def lookup_domain(interaction: discord.Interaction, domain_name: str):
    await interaction.response.defer(thinking=True)
    
    # 💡 リストではなく文字列として確実に処理
    domain = domain_name.strip().replace("http://", "").replace("https://", "").split("/")[0]
    
    url = f"https://rdap.org{domain}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status != 200:
                    await interaction.followup.send("⚠️ ドメイン情報が見つからないか、非対応の拡張子です。")
                    return
                data = await response.json()
        
        events = data.get("events", [])
        created = updated = expires = "不明"
        for event in events:
            action = event.get("eventAction")
            date = event.get("eventDate", "").replace("T", " ").replace("Z", "")
            if action == "registration": created = date
            elif action == "last changed": updated = date
            elif action == "expiration": expires = date

        embed = discord.Embed(title=f"🌐 ドメイン情報検索: {domain}", color=discord.Color.purple())
        embed.add_field(name="📅 登録日 (作成日)", value=created, inline=True)
        embed.add_field(name="🔄 最終更新日", value=updated, inline=True)
        embed.add_field(name="⏳ 有効期限", value=expires, inline=True)
        await interaction.followup.send(embed=embed)
    except Exception as e:
        await interaction.followup.send(f"⚠️ エラーが発生しました: {e}")

# --- 6. URL安全チェックコマンド（バグ完全修正版） ---
@bot.tree.command(name="check_url", description="URLの安全性をスキャンします")
@app_commands.describe(target_url="チェックしたいURL（例: http://example.com）")
async def check_url(interaction: discord.Interaction, target_url: str):
    await interaction.response.defer(thinking=True)
    api_key = os.environ.get("VIRUSTOTAL_API_KEY")
    if not api_key:
        await interaction.followup.send("⚠️ VirusTotalのAPIキーが設定されていません。環境変数を確認してください。")
        return
    
    headers = {"x-apikey": api_key}
    raw_url = target_url.strip()
    url_id = base64.urlsafe_b64encode(raw_url.encode('utf-8')).decode('utf-8').rstrip("=")
    api_url = f"https://virustotal.com{url_id}"
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(api_url, headers=headers) as response:
                if response.status == 404:
                    scan_url = "https://virustotal.com"
                    async with session.post(scan_url, headers=headers, data={"url": raw_url}) as p_resp:
                        if p_resp.status == 200:
                            await interaction.followup.send("🔄 このURLは初めてチェックされました。現在解析中ですので、数十秒後にもう一度試してください！")
                            return
                if response.status != 200:
                    await interaction.followup.send(f"⚠️ 安全チェックサーバーとの通信に失敗しました。(Status: {response.status})")
                    return
                data = await response.json()
        
        stats = data["data"]["attributes"]["last_analysis_stats"]
        malicious = stats.get("malicious", 0)
        suspicious = stats.get("suspicious", 0)
        harmless = stats.get("harmless", 0)
        
        if malicious > 0 or suspicious > 0:
            embed = discord.Embed(title="🚨 危険URL警告", color=discord.Color.red(), description=f"対象URL: {raw_url}")
            embed.add_field(name="❌ 危険・詐欺判定件数", value=f"**{malicious}** 件のエンジンが危険と判定", inline=False)
            embed.add_field(name="⚠️ 怪しい判定件数", value=f"{suspicious} 件", inline=True)
        else:
            embed = discord.Embed(title="🟢 安全なURL", color=discord.Color.green(), description=f"対象URL: {raw_url}")
embed.add_field(name="✅ 判定結果", value="危険な要素は見つかりませんでした（安全）", inline=False)
embed.add_field(name="🔍 スキャン数", value=f"合計 {malicious + suspicious + harmless} 個のセキュリティエンジンで検証", inline=False)
await interaction.followup.send(embed=embed)
except Exception as e:
await interaction.followup.send(f"⚠️ エラーが発生しました: {e}")

--- 7. 起動処理 ---

if name == "main":
web_thread = threading.Thread(target=run_web)
web_thread.daemon = True
web_thread.start()
token = os.environ.get("DISCORD_TOKEN")
bot.run(token)
