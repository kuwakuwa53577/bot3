import os
import threading
import socket
import asyncio
import aiohttp
import discord
from discord.ext import commands
from discord import app_commands
from flask import Flask
import base64
import json
import re
import logging

import firebase_admin
from firebase_admin import credentials, firestore

# ログ設定
logging.basicConfig(level=logging.INFO)

# --------------------------------------------------
# 1. 設定 & 環境変数
# --------------------------------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN") or os.getenv("DISCORD_BOT_TOKEN")
PROXY_URL = os.getenv("PROXY_URL")
PORT = int(os.getenv("PORT", 10000))
TARGET_REACTION_CHANNEL_ID = int(os.getenv("TARGET_REACTION_CHANNEL_ID", "0"))

# --------------------------------------------------
# 2. Firebase 初期化
# --------------------------------------------------
firebase_creds_json = os.getenv("FIREBASE_CREDENTIALS")
if firebase_creds_json:
    try:
        cred_dict = json.loads(firebase_creds_json)
        if "private_key" in cred_dict:
            cred_dict["private_key"] = cred_dict["private_key"].replace("\\n", "\n")
        cred = credentials.Certificate(cred_dict)
        firebase_admin.initialize_app(cred)
        print("✅ Firebase Admin SDK の初期化に成功しました")
    except Exception as e:
        print(f"❌ Firebase 初期化エラー: {e}")
elif os.path.exists("serviceAccountKey.json"):
    try:
        cred = credentials.Certificate("serviceAccountKey.json")
        firebase_admin.initialize_app(cred)
        print("✅ ローカルファイルで Firebase の初期化に成功しました")
    except Exception as e:
        print(f"❌ Firebase 初期化エラー: {e}")

db = None
try:
    if firebase_admin._apps:
        db = firestore.client()
        print("✅ Firestoreクライアントの取得に成功しました")
except Exception as e:
    print(f"⚠️ Firestoreクライアントの取得に失敗しました: {e}")

# データベースから指定ユーザーのIPを取得するヘルパー関数
def get_user_ip_from_db(user_id: int) -> str:
    if not db:
        return "データベース未接続"
    try:
        doc_ref = db.collection("verifications").document(str(user_id))
        doc = doc_ref.get()
        if doc.exists:
            data = doc.to_dict()
            return data.get("ip", "IP未記録")
    except Exception as e:
        print(f"❌ DB読み込みエラー: {e}")
    return "IP取得失敗"

# --------------------------------------------------
# 3. Flask 簡易Webサーバー（Renderポート対策用）
# --------------------------------------------------
app = Flask(__name__)

@app.route("/")
@app.route("/ping")
def ping():
    return "Bot is running!", 200

# --------------------------------------------------
# 4. Discord Bot クラス
# --------------------------------------------------
class MyBot(commands.Bot):
    async def setup_hook(self):
        try:
            synced = await self.tree.sync()
            print(f"✅ スラッシュコマンド同期完了: {len(synced)} 件")
        except Exception as e:
            print(f"Failed to sync commands: {e}")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = MyBot(command_prefix="!", intents=intents, proxy=PROXY_URL)

@bot.event
async def on_ready():
    print("========================================")
    print(f"✅ Discord Bot 接続成功: {bot.user}")
    print("========================================")

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return

    if TARGET_REACTION_CHANNEL_ID != 0 and message.channel.id == TARGET_REACTION_CHANNEL_ID:
        try:
            await message.add_reaction("👍")
        except Exception:
            pass

    await bot.process_commands(message)

# --------------------------------------------------
# 5. 便利ツール・解析コマンド群 (/ip, /zip, /domain, /dns, /check_url, /status, /lookup)
# --------------------------------------------------

# ① IPアドレス詳細完全解析コマンド
@bot.tree.command(name="ip", description="指定されたIPアドレスから取得できるすべての情報を徹底的に解析します")
@app_commands.describe(ip_address="検索したいIPアドレス（例: 8.8.8.8）")
async def lookup_ip(interaction: discord.Interaction, ip_address: str):
    await interaction.response.defer(thinking=True)
    target_ip = ip_address.strip().replace(" ", "")
    ip_url = f"http://ip-api.com/json/{target_ip}"
    ip_params = {"lang": "ja", "fields": "66846719"}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(ip_url, params=ip_params) as response:
                if response.status != 200:
                    await interaction.followup.send("⚠️ 外部データサーバーとの通信に失敗しました。")
                    return
                data = await response.json(content_type=None)

        if data.get("status") == "fail":
            await interaction.followup.send(f"⚠️ 情報を取得できませんでした。理由: {data.get('message', '不明なエラー')}")
            return

        try:
            hostname = await asyncio.to_thread(socket.gethostbyaddr, target_ip)
            hostname_str = hostname[0]
        except Exception:
            hostname_str = "逆引きできませんでした（PTRレコードなし）"

        zip_code = data.get("zip", "").strip().replace("-", "").replace(" ", "")
        full_address = "不明または日本国外"
        
        if zip_code and len(zip_code) == 7 and zip_code.isdigit():
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get("https://zipcloud.ibsnet.co.jp/api/search", params={"zipcode": zip_code}) as z_resp:
                        if z_resp.status == 200:
                            z_data = await z_resp.json(content_type=None)
                            if z_data.get("status") == 200 and z_data.get("results"):
                                res = z_data["results"][0]
                                full_address = f"{res.get('address1', '')}{res.get('address2', '')}{res.get('address3', '')}"
            except Exception:
                pass

        embed = discord.Embed(title=f"🌐 IPアドレス完全解析結果: {data.get('query', target_ip)}", color=discord.Color.blue())
        embed.add_field(name="📍 地理・住所情報", value=f"**自動逆引き住所:** {full_address}\n**国名:** {data.get('country', '不明')}\n**都道府県:** {data.get('regionName', '不明')}\n**市区町村:** {data.get('city', '不明')}\n**郵便番号:** 〒{data.get('zip', '不明')}", inline=False)
        embed.add_field(name="🏢 通信・ネットワーク情報", value=f"**ホスト名 (PTR):** `{hostname_str}`\n**プロバイダ (ISP):** {data.get('isp', '不明')}", inline=False)
        await interaction.followup.send(embed=embed)
    except Exception as e:
        await interaction.followup.send(f"⚠️ エラーが発生しました: {e}")

# ② 郵便番号検索コマンド
@bot.tree.command(name="zip", description="日本の郵便番号から詳細な住所情報を検索します")
@app_commands.describe(zip_code="検索したい7桁の郵便番号（例: 100-0005）")
async def lookup_zip(interaction: discord.Interaction, zip_code: str):
    await interaction.response.defer(thinking=True)
    clean_zip = zip_code.strip().replace("-", "").replace(" ", "")
    if not clean_zip.isdigit() or len(clean_zip) != 7:
        await interaction.followup.send("⚠️ 郵便番号は7桁の数字で入力してください。")
        return
        
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("https://zipcloud.ibsnet.co.jp/api/search", params={"zipcode": clean_zip}) as response:
                if response.status != 200:
                    await interaction.followup.send("⚠️ 郵便番号サーバーとの通信に失敗しました。")
                    return
                data = await response.json(content_type=None)
                
        if data.get("status") != 200 or not data.get("results"):
            await interaction.followup.send("⚠️ 該当する住所が見つかりませんでした。")
            return
            
        res = data["results"][0]
        full_address = f"{res.get('address1', '')}{res.get('address2', '')}{res.get('address3', '')}"
        embed = discord.Embed(title=f"📮 郵便番号検索: 〒{clean_zip}", color=discord.Color.green())
        embed.add_field(name="📍 住所", value=full_address, inline=False)
        await interaction.followup.send(embed=embed)
    except Exception as e:
        await interaction.followup.send(f"⚠️ エラーが発生しました: {e}")

# ③ ドメイン検索コマンド (/domain)
@bot.tree.command(name="domain", description="WEBサイトのドメイン情報を検索します")
@app_commands.describe(domain_name="検索したいドメイン（例: google.com）")
async def lookup_domain(interaction: discord.Interaction, domain_name: str):
    await interaction.response.defer(thinking=True)
    domain = domain_name.strip().replace("http://", "").replace("https://", "").split("/")[0]
    tld = domain.split(".")[-1].lower()

    try:
        async with aiohttp.ClientSession() as session:
            iana_url = f"https://rdap.iana.org/domain/{tld}"
            async with session.get(iana_url, headers={"Accept": "application/json"}) as iana_resp:
                if iana_resp.status != 200:
                    await interaction.followup.send("⚠️ 非対応の拡張子、またはドメインが見つかりませんでした。")
                    return
                iana_data = await iana_resp.json(content_type=None)
            
            target_rdap_url = None
            for link in iana_data.get("links", []):
                href = link.get("href", "")
                if "rdap" in href and href.startswith("https://"):
                    target_rdap_url = href.rstrip("/")
                    break
            
            if not target_rdap_url:
                target_rdap_url = "https://rdap.org"

            domain_query_url = f"{target_rdap_url}/domain/{domain}"
            async with session.get(domain_query_url, headers={"Accept": "application/json"}) as domain_resp:
                if domain_resp.status != 200:
                    async with session.get(f"https://rdap.org/domain/{domain}", headers={"Accept": "application/json"}) as fallback_resp:
                        if fallback_resp.status == 200:
                            data = await fallback_resp.json(content_type=None)
                        else:
                            await interaction.followup.send("⚠️ 該当ドメインの登録情報が見つかりませんでした。")
                            return
                else:
                    data = await domain_resp.json(content_type=None)
        
        created = updated = expires = "不明"
        for event in data.get("events", []):
            action = event.get("eventAction")
            date = event.get("eventDate", "").replace("T", " ").replace("Z", "")
            if action == "registration": created = date
            elif action == "last changed": updated = date
            elif action == "expiration": expires = date

        embed = discord.Embed(title=f"🌐 ドメイン情報検索: {domain}", color=discord.Color.purple())
        embed.add_field(name="📅 登録日", value=created, inline=True)
        embed.add_field(name="🔄 最終更新日", value=updated, inline=True)
        embed.add_field(name="⏳ 有効期限", value=expires, inline=True)
        await interaction.followup.send(embed=embed)
    except Exception as e:
        await interaction.followup.send(f"⚠️ エラーが発生しました: {e}")

# ④ DNSレコード検索コマンド (/dns)
@bot.tree.command(name="dns", description="指定されたドメインのIPアドレスやDNS情報を解決します")
@app_commands.describe(domain_name="検索したいドメイン名（例: example.com）")
async def lookup_dns(interaction: discord.Interaction, domain_name: str):
    await interaction.response.defer(thinking=True)
    target = domain_name.strip().replace("http://", "").replace("https://", "").split("/")[0]
    
    try:
        addr_info = await asyncio.to_thread(socket.getaddrinfo, target, None)
        ipv4_list, ipv6_list = set(), set()
        
        for family, _, _, _, sockaddr in addr_info:
            if family == socket.AF_INET:
                ipv4_list.add(sockaddr[0])
            elif family == socket.AF_INET6:
                ipv6_list.add(sockaddr[0])
        
        try:
            hostname = await asyncio.to_thread(socket.gethostbyaddr, list(ipv4_list)[0] if ipv4_list else target)
            ptr_name = hostname[0]
        except Exception:
            ptr_name = "逆引きなし"

        embed = discord.Embed(title=f"🔍 DNS解決結果: {target}", color=discord.Color.gold())
        embed.add_field(name="🌐 IPv4アドレス (Aレコード)", value="\n".join(ipv4_list) if ipv4_list else "見つかりませんでした", inline=False)
        embed.add_field(name="🌍 IPv6アドレス (AAAAレコード)", value="\n".join(ipv6_list) if ipv6_list else "見つかりませんでした", inline=False)
        embed.add_field(name="🔄 ホスト名 (PTR)", value=f"`{ptr_name}`", inline=False)
        await interaction.followup.send(embed=embed)
    except socket.gaierror:
        await interaction.followup.send(f"⚠️ 指定されたドメイン `{target}` のIPアドレスを解決できませんでした。")
    except Exception as e:
        await interaction.followup.send(f"⚠️ エラーが発生しました: {e}")

# ⑤ URL安全チェックコマンド (/check_url)
@bot.tree.command(name="check_url", description="URLの安全性をVirusTotalでスキャンします")
@app_commands.describe(target_url="チェックしたいURL")
async def check_url(interaction: discord.Interaction, target_url: str):
    await interaction.response.defer(thinking=True)
    api_key = os.environ.get("VIRUSTOTAL_API_KEY")
    if not api_key:
        await interaction.followup.send("⚠️ VirusTotalのAPIキーが設定されていません。")
        return
    
    headers = {"x-apikey": api_key}
    raw_url = target_url.strip()
    url_id = base64.urlsafe_b64encode(raw_url.encode('utf-8')).decode('utf-8').rstrip("=")
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"https://www.virustotal.com/api/v3/urls/{url_id}", headers=headers) as response:
                if response.status != 200:
                    await interaction.followup.send("⚠️ サーバーとの通信に失敗しました。")
                    return
                data = await response.json(content_type=None)
        
        stats = data["data"]["attributes"]["last_analysis_stats"]
        malicious = stats.get("malicious", 0)
        
        if malicious > 0:
            embed = discord.Embed(title="🚨 危険URL警告", color=discord.Color.red(), description=f"対象: {raw_url}")
            embed.add_field(name="❌ 危険判定件数", value=f"**{malicious}** 件", inline=False)
        else:
            embed = discord.Embed(title="🟢 安全なURL", color=discord.Color.green(), description=f"対象: {raw_url}")
            embed.add_field(name="✅ 判定", value="安全です", inline=False)
        await interaction.followup.send(embed=embed)
    except Exception as e:
        await interaction.followup.send(f"⚠️ エラーが発生しました: {e}")

# ⑥ 障害ステータス確認コマンド (/status)
@bot.tree.command(name="status", description="DiscordやGitHubなどの障害ステータスを確認します")
async def check_status(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)
    urls = {
        "discord": "https://discordstatus.com/api/v2/summary.json",
        "github": "https://www.githubstatus.com/api/v2/summary.json",
        "cloudflare": "https://www.cloudflarestatus.com/api/v2/summary.json",
        "render": "https://status.render.com/api/v2/summary.json"
    }
    results = {}
    try:
        async with aiohttp.ClientSession() as session:
            for key, url in urls.items():
                try:
                    async with session.get(url, timeout=5) as resp:
                        if resp.status == 200:
                            results[key] = await resp.json(content_type=None)
                except Exception:
                    pass

        embed = discord.Embed(title="🛠️ 各種サービス稼働状況", color=discord.Color.dark_embed())
        for name, data in results.items():
            status_text = "🟢 正常稼働中" if data and data.get("status", {}).get("indicator") == "none" else "⚠️ 要確認"
            embed.add_field(name=name.capitalize(), value=status_text, inline=False)
        await interaction.followup.send(embed=embed)
    except Exception as e:
        await interaction.followup.send(f"⚠️ エラー: {e}")

# --------------------------------------------------
# 6. 管理者専用 メンバー位置情報特定コマンド (/userinfo)
# --------------------------------------------------
@bot.tree.command(name="userinfo", description="【管理者専用】Firebaseの登録IPから対象メンバーの大まかなエリアや住所情報を特定します")
@app_commands.allowed_installs(guilds=True, users=False)
@app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
@app_commands.default_permissions(administrator=True)
@app_commands.describe(member="調査したいDiscordメンバーを選択してください")
async def user_info_command(interaction: discord.Interaction, member: discord.Member):
    await interaction.response.defer(thinking=True, ephemeral=True)
    
    try:
        guild = interaction.guild
        if not guild:
            await interaction.followup.send("❌ サーバー内で実行してください。", ephemeral=True)
            return

        # Firebaseから対象メンバーのIPを取得
        user_ip = get_user_ip_from_db(member.id)
        
        ip_location = "不明または未検証"
        detailed_address = "特定不可（IPデータなし）"
        isp_info = "不明"
        google_maps_url = None

        if user_ip and user_ip not in ["データベース未接続", "IP未記録", "IP取得失敗"]:
            ip_url = f"http://ip-api.com/json/{user_ip}"
            ip_params = {"lang": "ja", "fields": "66846719"}
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(ip_url, params=ip_params, timeout=5) as resp:
                        if resp.status == 200:
                            data = await resp.json(content_type=None)
                            if data.get("status") == "success":
                                country = data.get("country", "")
                                region = data.get("regionName", "")
                                city = data.get("city", "")
                                district = data.get("district", "")
                                ip_location = f"{country} {region} {city} {district}".strip()
                                isp_info = f"{data.get('isp', '不明')} ({data.get('org', '')})"
                                
                                zip_code = data.get("zip", "").strip()
                                if zip_code and len(zip_code) == 7 and zip_code.isdigit():
                                    async with session.get("https://zipcloud.ibsnet.co.jp/api/search", params={"zipcode": zip_code}, timeout=5) as z_resp:
                                        if z_resp.status == 200:
                                            z_data = await z_resp.json(content_type=None)
                                            if z_data.get("status") == 200 and z_data.get("results"):
                                                res = z_data["results"][0]
                                                detailed_address = f"〒{zip_code[:3]}-{zip_code[3:]} ➔ **{res.get('address1', '')}{res.get('address2', '')}{res.get('address3', '')}**"
                                
                                lat = data.get("lat")
                                lon = data.get("lon")
                                if lat and lon:
                                    google_maps_url = f"https://www.google.com/maps/search/?api=1&query={lat},{lon}"
            except Exception:
                pass

        roles = [r.mention for r in member.roles if r.name != "@everyone"]
        roles_str = ", ".join(roles) if roles else "ロールなし"
        created_at = member.created_at.strftime("%Y年%m月%d日 %H:%M:%S")
        joined_at = member.joined_at.strftime("%Y年%m月%d日 %H:%M:%S") if member.joined_at else "不明"

        embed = discord.Embed(
            title=f"🔍 メンバー位置情報特定: {member.display_name}",
            color=discord.Color.red(),
            description=f"対象ユーザー: {member.mention} (`{member.id}`)\n※Firebaseのデータベース（`verifications`）からIPを抽出して特定しています。"
        )
        
        if member.avatar:
            embed.set_thumbnail(url=member.avatar.url)

        embed.add_field(name="📍 データベースIPに基づく特定住所", value=f"### `{detailed_address if detailed_address != '特定不可（IPデータなし）' else ip_location}`", inline=False)
        embed.add_field(name="🌐 ネットワーク・ISP情報", value=f"**記録されたIP:** `{user_ip}`\n**推定エリア:** {ip_location}\n**プロバイダ (ISP):** {isp_info}", inline=False)

        if google_maps_url:
            embed.add_field(name="🗺️ マップ確認", value=f"[推定位置をGoogle Mapsで開く]({google_maps_url})", inline=False)

        embed.add_field(name="📅 Discordアカウント履歴", value=f"**アカウント作成日:** {created_at}\n**サーバー参加日:** {joined_at}", inline=False)
        embed.add_field(name="🛡️ 所持ロール", value=roles_str, inline=False)

        embed.set_footer(text=f"管理者専用機能 | 実行者: {interaction.user.name}")
        await interaction.followup.send(embed=embed, ephemeral=True)

    except Exception as e:
        await interaction.followup.send(f"⚠️ エラーが発生しました: `{e}`", ephemeral=True)

# --------------------------------------------------
# 7. 起動処理
# --------------------------------------------------
async def start_bot_with_retry():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN が環境変数に設定されていません。")

    while True:
        try:
            print("🚀 Discord Bot に接続を試みています...")
            await bot.start(BOT_TOKEN)
            break
        except Exception as e:
            print(f"❌ 接続エラー: {e}。15秒後に再試行します...")
            await asyncio.sleep(15)

async def main():
    flask_thread = threading.Thread(target=lambda: app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False))
    flask_thread.daemon = True
    flask_thread.start()
    print(f"🌐 Flask サーバーをポート {PORT} で起動しました")

    await start_bot_with_retry()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("👋 終了します。")
