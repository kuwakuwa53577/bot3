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

import firebase_admin
from firebase_admin import credentials, firestore

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
intents.members = True 

TARGET_REACTION_CHANNEL_ID = int(os.environ.get("TARGET_REACTION_CHANNEL_ID", "0"))
TARGET_CREATE_VC_ID = int(os.environ.get("TARGET_CREATE_VC_ID", "0"))

auth_env = os.environ.get("AUTHORIZED_USER_IDS", "")
if auth_env:
    AUTHORIZED_USER_IDS = {int(uid.strip()) for uid in auth_env.split(",") if uid.strip().isdigit()}
else:
    AUTHORIZED_USER_IDS = {123456789012345678}

message_spam_track = {}

# --- Firebase 初期化 ---
firebase_creds_json = os.environ.get("FIREBASE_CREDENTIALS")
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

try:
    db = firestore.client()
except Exception as e:
    db = None
    print(f"⚠️ Firestoreクライアントの取得に失敗しました: {e}")

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

class ReactionRolePersistentView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

class MyBot(commands.Bot):
    async def setup_hook(self):
        self.add_view(ReactionRolePersistentView())
        try:
            synced = await self.tree.sync()
            print(f"Successfully synced {len(synced)} command(s).")
        except Exception as e:
            print(f"Failed to sync commands: {e}")

bot = MyBot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")

def is_authorized_or_admin(member: discord.Member) -> bool:
    if not member:
        return False
    if member.id in AUTHORIZED_USER_IDS:
        return True
    if member.guild_permissions.administrator:
        return True
    return False

# --- 自動イイネ（リアクション）機能 ---
@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return

    if TARGET_REACTION_CHANNEL_ID != 0 and message.channel.id == TARGET_REACTION_CHANNEL_ID:
        try:
            await message.add_reaction("👍")
        except discord.Forbidden:
            print("❌ リアクションを追加する権限がありません。")
        except Exception as e:
            print(f"⚠️ リアクション付与中にエラーが発生しました: {e}")

    await bot.process_commands(message)


# --- 3. IPアドレス詳細完全解析コマンド（すべての情報を網羅） ---
@bot.tree.command(name="ip", description="指定されたIPアドレスから取得できるすべての情報を徹底的に解析します")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
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
        pref_code = data.get("prefcode", "不明")
        
        if zip_code and len(zip_code) == 7 and zip_code.isdigit():
            zip_url = "https://zipcloud.ibsnet.co.jp/api/search"
            zip_params = {"zipcode": zip_code}
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(zip_url, params=zip_params) as z_resp:
                        if z_resp.status == 200:
                            z_data = await z_resp.json(content_type=None)
                            if z_data.get("status") == 200 and z_data.get("results"):
                                res = z_data["results"][0]
                                full_address = f"{res.get('address1', '')}{res.get('address2', '')}{res.get('address3', '')}"
                                pref_code = res.get('prefcode', pref_code)
            except Exception:
                full_address = "住所逆引き中にエラーが発生しました"

        embed = discord.Embed(
            title=f"🌐 IPアドレス完全解析結果: {data.get('query', target_ip)}", 
            color=discord.Color.blue(),
            description="IPジオロケーションAPIおよびDNS逆引きから抽出した全データ"
        )
        
        embed.add_field(
            name="📍 地理・住所情報", 
            value=(
                f"**自動逆引き住所:** {full_address}\n"
                f"**国名:** {data.get('country', '不明')} ({data.get('countryCode', '-')})\n"
                f"**都道府県/州:** {data.get('regionName', '不明')} (JISコード: {pref_code})\n"
                f"**市区町村:** {data.get('city', '不明')}\n"
                f"**地域/地区:** {data.get('district', 'なし')}\n"
                f"**郵便番号:** 〒{data.get('zip', '不明')}"
            ), 
            inline=False
        )
        
        embed.add_field(
            name="🧭 座標・時差・通貨", 
            value=(
                f"**緯度 / 経度:** {data.get('lat', '-')}, {data.get('lon', '-')}\n"
                f"**タイムゾーン:** {data.get('timezone', '不明')}\n"
                f"**UTC時差:** {data.get('offset', 0) / 3600:+g} 時間 ({data.get('offset', 0)}秒)\n"
                f"**使用通貨:** {data.get('currency', '不明')}"
            ), 
            inline=False
        )
        
        embed.add_field(
            name="🏢 通信・ネットワーク情報", 
            value=(
                f"**ホスト名 (PTR):** `{hostname_str}`\n"
                f"**プロバイダ (ISP):** {data.get('isp', '不明')}\n"
                f"**組織名 (Org):** {data.get('org', '不明')}\n"
                f"**AS番号:** {data.get('as', '不明')}\n"
                f"**AS組織名:** {data.get('asname', '不明')}"
            ), 
            inline=False
        )
        
        is_mobile = "🔴 モバイル回線 (4G/5G/LTE)" if data.get("mobile") else "🟢 固定回線 / ブロードバンド"
        is_proxy = "⚠️ VPN・プロキシ検出" if data.get("proxy") else "🟢 通常の接続"
        is_hosting = "⚠️ データセンター（サーバー・VPS）" if data.get("hosting") else "🟢 通常の一般回線"
        
        embed.add_field(
            name="🚨 回線種類・脅威判定フラグ", 
            value=(
                f"**回線タイプ:** {is_mobile}\n"
                f"**匿名化ツール:** {is_proxy}\n"
                f"**ホスティング:** {is_hosting}"
            ), 
            inline=False
        )
        
        embed.set_footer(text=f"APIステータス: {data.get('status')} | 取得フィールド数: 20+項目の網羅的解析")
        await interaction.followup.send(embed=embed)
        
    except Exception as e:
        await interaction.followup.send(f"⚠️ 予期せぬエラーが発生しました: {e}")
        
# --- 4. 郵便番号検索コマンド ---
@bot.tree.command(name="zip", description="日本の郵便番号から行政コードや詳細な住所情報を検索します")
@app_commands.describe(zip_code="検索したい7桁の郵便番号（例: 100-0005）")
async def lookup_zip(interaction: discord.Interaction, zip_code: str):
    await interaction.response.defer(thinking=True)
    clean_zip = zip_code.strip().replace("-", "").replace(" ", "")
    if not clean_zip.isdigit() or len(clean_zip) != 7:
        await interaction.followup.send("⚠️ 郵便番号は7桁の数字で入力してください。")
        return
        
    base_url = "https://zipcloud.ibsnet.co.jp/api/search"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(base_url, params={"zipcode": clean_zip}) as response:
                if response.status != 200:
                    await interaction.followup.send("⚠️ 郵便番号サーバーとの通信に失敗しました。")
                    return
                data = await response.json(content_type=None)
                
        if data.get("status") != 200 or not data.get("results"):
            await interaction.followup.send("⚠️ 該当する住所が見つかりませんでした。")
            return
                
        res = data["results"][0]
        pref_code = res.get('prefcode', '不明')
        zip7 = res.get('zipcode', clean_zip)
        
        address1 = res.get('address1', '')
        address2 = res.get('address2', '')
        address3 = res.get('address3', '')
        full_address = f"{address1}{address2}{address3}"
        
        kana1 = res.get('kana1', '')
        kana2 = res.get('kana2', '')
        kana3 = res.get('kana3', '')
        full_kana = f"{kana1} {kana2} {kana3}"

        embed = discord.Embed(title=f"📮 郵便番号詳細解析結果: 〒{zip7[:3]}-{zip7[3:]}", color=discord.Color.green())
        embed.add_field(name="📍 住所・行政コード情報", value=(
            f"**完全な住所:** {full_address}\n"
            f"**都道府県:** {address1} (JIS都道府県コード: {pref_code})\n"
            f"**市区町村:** {address2}\n"
            f"**町域:** {address3 if address3 else '指定なし'}"
        ), inline=False)
        embed.add_field(name="🔤 カナ（読み）情報", value=f"`{full_kana}`", inline=False)

        await interaction.followup.send(embed=embed)
    except Exception as e:
        await interaction.followup.send(f"⚠️ エラーが発生しました: {e}")

# --- 5. ドメイン検索コマンド ---
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
            
            rdap_servers = iana_data.get("links", [])
            target_rdap_url = None
            for link in rdap_servers:
                href = link.get("href", "")
                if "rdap" in href and href.startswith("https://"):
                    target_rdap_url = href.rstrip("/")
                    break
            
            if not target_rdap_url:
                target_rdap_url = f"https://rdap.org"

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
        
        events = data.get("events", [])
        created = updated = expires = "不明"
        for event in events:
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

# --- 6. DNSレコード検索コマンド ---
@bot.tree.command(name="dns", description="指定されたドメインのIPアドレスやDNS情報を解決します")
@app_commands.describe(domain_name="検索したいドメイン名（例: example.com）")
async def lookup_dns(interaction: discord.Interaction, domain_name: str):
    await interaction.response.defer(thinking=True)
    target = domain_name.strip().replace("http://", "").replace("https://", "").split("/")[0]
    
    try:
        addr_info = await asyncio.to_thread(socket.getaddrinfo, target, None)
        
        ipv4_list = set()
        ipv6_list = set()
        
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

# --- 7. URL安全チェックコマンド ---
@bot.tree.command(name="check_url", description="URLの安全性をスキャンします")
@app_commands.describe(target_url="チェックしたいURL（例: http://example.com）")
async def check_url(interaction: discord.Interaction, target_url: str):
    await interaction.response.defer(thinking=True)
    api_key = os.environ.get("VIRUSTOTAL_API_KEY")
    if not api_key:
        await interaction.followup.send("⚠️ VirusTotalのAPIキーが設定されていません。")
        return
    
    headers = {"x-apikey": api_key}
    raw_url = target_url.strip()
    url_id = base64.urlsafe_b64encode(raw_url.encode('utf-8')).decode('utf-8').rstrip("=")
    api_url = f"https://www.virustotal.com/api/v3/urls/{url_id}"
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(api_url, headers=headers) as response:
                if response.status == 404:
                    scan_url = "https://www.virustotal.com/api/v3/urls"
                    async with session.post(scan_url, headers=headers, data={"url": raw_url}) as p_resp:
                        if p_resp.status == 200:
                            await interaction.followup.send("🔄 このURLは初めてチェックされました。解析中ですので後ほど再試行してください。")
                            return
                if response.status != 200:
                    await interaction.followup.send("⚠️ 安全チェックサーバーとの通信に失敗しました。")
                    return
                data = await response.json(content_type=None)
        
        stats = data["data"]["attributes"]["last_analysis_stats"]
        malicious = stats.get("malicious", 0)
        suspicious = stats.get("suspicious", 0)
        
        if malicious > 0 or suspicious > 0:
            embed = discord.Embed(title="🚨 危険URL警告", color=discord.Color.red(), description=f"対象URL: {raw_url}")
            embed.add_field(name="❌ 危険・詐欺判定件数", value=f"**{malicious}** 件", inline=False)
        else:
            embed = discord.Embed(title="🟢 安全なURL", color=discord.Color.green(), description=f"対象URL: {raw_url}")
            embed.add_field(name="✅ 判定結果", value="安全です", inline=False)
        
        await interaction.followup.send(embed=embed)
    except Exception as e:
        await interaction.followup.send(f"⚠️ エラーが発生しました: {e}")

# --- 10. 主要サービス（Discord, GitHub, Cloudflare, Render）の障害状況確認コマンド ---
@bot.tree.command(name="status", description="Discord, GitHub, Cloudflare, Renderの障害状況や稼働ステータスを確認します")
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
                        else:
                            results[key] = None
                except Exception:
                    results[key] = None
    except Exception as e:
        await interaction.followup.send(f"⚠️ 通信セッションの確立に失敗しました: {e}")
        return
        
    try:
        embed = discord.Embed(title="🛠️ 各種サービス障害・稼働状況", color=discord.Color.dark_embed())
        
        def parse_status(data, name):
            if not data:
                return f"⚠️ ステータス情報の取得に失敗しました。"
            
            status_info = data.get("status", {})
            indicator = status_info.get("indicator", "none")
            description = status_info.get("description", "不明")
            
            if indicator == "none":
                text = f"🟢 **正常稼働中**\n説明: {description}"
            elif indicator == "minor":
                text = f"🟡 **軽微な障害発生中**\n説明: {description}"
            elif indicator == "major":
                text = f"🟠 **重大な障害発生中**\n説明: {description}"
            else:
                text = f"🔴 **大規模な障害発生中**\n説明: {description}"
                
            incidents = data.get("incidents", [])
            if incidents:
                active_incidents = [inc["name"] for inc in incidents if inc.get("status") != "resolved"]
                if active_incidents:
                    text += f"\n**アクティブなインシデント:**\n" + "\n".join([f"- {inc_name}" for inc_name in active_incidents])
            return text

        embed.add_field(name="💬 Discord ステータス", value=parse_status(results.get("discord"), "Discord"), inline=False)
        embed.add_field(name="🐙 GitHub ステータス", value=parse_status(results.get("github"), "GitHub"), inline=False)
        embed.add_field(name="☁️ Cloudflare ステータス", value=parse_status(results.get("cloudflare"), "Cloudflare"), inline=False)
        embed.add_field(name="🚀 Render ステータス", value=parse_status(results.get("render"), "Render"), inline=False)
        
        embed.set_footer(text="情報元: Atlassian Statuspage API (各公式ステータス)")
        await interaction.followup.send(embed=embed)
        
    except Exception as e:
        await interaction.followup.send(f"⚠️ 障害情報の構築中にエラーが発生しました: {e}")

# --- 11. テンポラリーボイスチャンネル機能の設定 ---
@bot.event
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
    if TARGET_CREATE_VC_ID == 0:
        return

    if after.channel and after.channel.id == TARGET_CREATE_VC_ID:
        guild = member.guild
        category = after.channel.category
        channel_name = f"💬│{member.display_name}の部屋"
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(connect=False),
            member: discord.PermissionOverwrite(connect=True, manage_channels=True, mute_members=True)
        }
        try:
            new_channel = await guild.create_voice_channel(
                name=channel_name,
                category=category,
                overwrites=overwrites,
                user_limit=5
            )
            await member.move_to(new_channel)
        except Exception as e:
            print(f"⚠️ 一時ボイスチャンネル作成エラー: {e}")

    if before.channel and before.channel.id != TARGET_CREATE_VC_ID:
        if before.channel.category and before.channel.category == (after.channel.category if after and after.channel else True):
            if "の部屋" in before.channel.name and len(before.channel.members) == 0:
                try:
                    await before.channel.delete()
                except Exception as e:
                    print(f"⚠️ 一時ボイスチャンネル削除エラー: {e}")


# --- 12. 2人きりのプライベートチャンネル作成コマンド ---
@bot.tree.command(name="private", description="指定したユーザーとあなただけの秘密のテキストチャンネルを作成します")
@app_commands.describe(target_user="一緒に話したいユーザー")
async def create_private_room(interaction: discord.Interaction, target_user: discord.Member):
    await interaction.response.defer(thinking=True, ephemeral=True)
    guild = interaction.guild
    
    if not guild or target_user.bot or target_user.id == interaction.user.id:
        await interaction.followup.send("❌ 無効なユーザー指定です。", ephemeral=True)
        return

    channel_name = f"🔒│{interaction.user.name}-{target_user.name}"
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(read_messages=False),
        interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True),
        target_user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
        guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True)
    }
    
    try:
        category = interaction.channel.category if hasattr(interaction.channel, 'category') else None
        private_channel = await guild.create_text_channel(
            name=channel_name,
            category=category,
            overwrites=overwrites,
            topic=f"{interaction.user.mention} と {target_user.mention} のプライベートチャンネル"
        )
        
        await private_channel.send(
            f"🔐 **プライベートチャンネルを作成しました！**\n"
            f"{interaction.user.mention} さんと {target_user.mention} さんだけが閲覧・書き込みできます。"
        )
        
        await interaction.followup.send(f"✅ プライベートチャンネルを作成しました: {private_channel.mention}")
    except Exception as e:
        await interaction.followup.send(f"⚠️ エラーが発生しました: {e}")


# --- IPアドレス判定用ヘルパー関数 ---
def is_valid_ip(address: str) -> bool:
    ipv4_pattern = r'^(\d{1,3}\.){3}\d{1,3}$'
    ipv6_pattern = r'^[0-9a-fA-F:]+$'
    return bool(re.match(ipv4_pattern, address) or (':' in address and re.match(ipv6_pattern, address)))

# --- 13. IP・ドメイン・DNS 一括完全解析コマンド ---
@bot.tree.command(name="lookup", description="IPアドレスまたはドメインを入力し、IP位置情報・DNS・WHOIS情報を一括解析します")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.describe(target="解析したいIPアドレスまたはドメイン名（例: 8.8.8.8 または google.com）")
async def lookup_all(interaction: discord.Interaction, target: str):
    await interaction.response.defer(thinking=True)
    
    clean_target = target.strip().replace("http://", "").replace("https://", "").split("/")[0].split(":")[0]
    
    target_ip = None
    target_domain = None
    
    if is_valid_ip(clean_target):
        target_ip = clean_target
    else:
        target_domain = clean_target

    ipv4_list = set()
    ipv6_list = set()
    ptr_hostname = "逆引きなし"
    
    dns_target = target_domain if target_domain else target_ip
    try:
        addr_info = await asyncio.to_thread(socket.getaddrinfo, dns_target, None)
        for family, _, _, _, sockaddr in addr_info:
            if family == socket.AF_INET:
                ipv4_list.add(sockaddr[0])
            elif family == socket.AF_INET6:
                ipv6_list.add(sockaddr[0])
                
        if not target_ip and ipv4_list:
            target_ip = list(ipv4_list)[0]
    except Exception:
        pass

    if target_ip:
        try:
            hostname = await asyncio.to_thread(socket.gethostbyaddr, target_ip)
            ptr_hostname = hostname[0]
            if not target_domain and "." in ptr_hostname:
                parts = ptr_hostname.split(".")
                if len(parts) >= 2:
                    target_domain = ".".join(parts[-2:])
        except Exception:
            ptr_hostname = "PTRレコードなし"

    ip_data = {}
    full_address = "不明または日本国外"
    if target_ip:
        ip_url = f"http://ip-api.com/json/{target_ip}"
        ip_params = {"lang": "ja", "fields": "66846719"}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(ip_url, params=ip_params) as resp:
                    if resp.status == 200:
                        ip_data = await resp.json(content_type=None)
                        
            zip_code = ip_data.get("zip", "").strip().replace("-", "").replace(" ", "")
            if zip_code and len(zip_code) == 7 and zip_code.isdigit():
                zip_url = "https://zipcloud.ibsnet.co.jp/api/search"
                async with aiohttp.ClientSession() as session:
                    async with session.get(zip_url, params={"zipcode": zip_code}) as z_resp:
                        if z_resp.status == 200:
                            z_data = await z_resp.json(content_type=None)
                            if z_data.get("status") == 200 and z_data.get("results"):
                                res = z_data["results"][0]
                                full_address = f"{res.get('address1', '')}{res.get('address2', '')}{res.get('address3', '')}"
        except Exception:
            pass

    domain_data = {}
    created_date = updated_date = expires_date = "不明"
    if target_domain:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"https://rdap.org/domain/{target_domain}", headers={"Accept": "application/json"}) as d_resp:
                    if d_resp.status == 200:
                        domain_data = await d_resp.json(content_type=None)
                        for event in domain_data.get("events", []):
                            action = event.get("eventAction")
                            date = event.get("eventDate", "").replace("T", " ").replace("Z", "")
                            if action == "registration": created_date = date
                            elif action == "last changed": updated_date = date
                            elif action == "expiration": expires_date = date
        except Exception:
            pass

    embed = discord.Embed(
        title=f"🔍 ネットワーク一括解析結果: {clean_target}",
        color=discord.Color.dark_purple(),
        description=f"対象: `{clean_target}`（IP解析 + DNS解決 + RDAPドメイン情報）"
    )

    dns_text = (
        f"**解析可能IP (v4):** {', '.join(ipv4_list) if ipv4_list else 'なし / 解決不能'}\n"
        f"**解析可能IP (v6):** {', '.join(ipv6_list) if ipv6_list else 'なし'}\n"
        f"**PTR (Reverse DNS):** `{ptr_hostname}`"
    )
    embed.add_field(name="🌐 DNS・IP解決情報", value=dns_text, inline=False)

    if ip_data and ip_data.get("status") == "success":
        is_mobile = "🔴 モバイル (4G/5G)" if ip_data.get("mobile") else "🟢 固定回線"
        is_proxy = "⚠️ VPN/プロキシ" if ip_data.get("proxy") else "🟢 通常接続"
        is_hosting = "⚠️ データセンター/VPS" if ip_data.get("hosting") else "🟢 一般回線"
        
        geo_text = (
            f"**対象IP:** `{ip_data.get('query', target_ip)}`\n"
            f"**位置:** {ip_data.get('country', '不明')} / {ip_data.get('regionName', '不明')} / {ip_data.get('city', '不明')}\n"
            f"**逆引き住所:** {full_address}\n"
            f"**座標 (緯度/経度):** {ip_data.get('lat', '-')}, {ip_data.get('lon', '-')}\n"
            f"**ISP / 組織:** {ip_data.get('isp', '不明')} / {ip_data.get('org', '不明')}\n"
            f"**AS情報:** {ip_data.get('as', '不明')}\n"
            f"**回線属性:** {is_mobile} | {is_proxy} | {is_hosting}"
        )
        embed.add_field(name="📍 IPジオロケーション & ネットワーク", value=geo_text, inline=False)

    if target_domain:
        domain_text = (
            f"**対象ドメイン:** `{target_domain}`\n"
            f"**登録日:** {created_date}\n"
            f"**最終更新:** {updated_date}\n"
            f"**有効期限:** {expires_date}"
        )
        embed.add_field(name="🏢 ドメイン(WHOIS/RDAP)情報", value=domain_text, inline=False)

    embed.set_footer(text="マルチデータソース: ip-api, zipcloud, RDAP, Local DNS")
    await interaction.followup.send(embed=embed)


# --- 管理者専用 メンバー位置情報（住所）特定コマンド ---
@discord_bot.tree.command(name="userinfo", description="【管理者専用】Firebaseの登録IPから対象メンバーの大まかなエリアや住所情報を特定します")
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

        # Firestoreからユーザーデータを取得
        all_data = load_all_data()
        user_info = all_data.get(str(member.id), {})
        user_ip = user_info.get("ip")
        
        ip_location = "不明または未検証"
        detailed_address = "特定不可（IPデータなし）"
        isp_info = "不明"
        google_maps_url = None

        if user_ip:
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

        # Discord側の基本情報
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
        embed.add_field(name="🌐 ネットワーク・ISP情報", value=f"**記録されたIP:** `{user_ip if user_ip else '未登録'}`\n**推定エリア:** {ip_location}\n**プロバイダ (ISP):** {isp_info}", inline=False)

        if google_maps_url:
            embed.add_field(name="🗺️ マップ確認", value=f"[推定位置をGoogle Mapsで開く]({google_maps_url})", inline=False)

        embed.add_field(name="📅 Discordアカウント履歴", value=f"**アカウント作成日:** {created_at}\n**サーバー参加日:** {joined_at}", inline=False)
        embed.add_field(name="🛡️ 所持ロール", value=roles_str, inline=False)

        embed.set_footer(text=f"管理者専用機能 | 実行者: {interaction.user.name}")
        await interaction.followup.send(embed=embed, ephemeral=True)

    except Exception as e:
        await interaction.followup.send(f"⚠️ エラーが発生しました: `{e}`", ephemeral=True)

# --- 8. 最大6個のロール対応リアクションロール設置コマンド ---
class MultiReactionRoleButton(discord.ui.Button):
    def __init__(self, role_id: int, label: str, emoji=None):
        super().__init__(style=discord.ButtonStyle.primary, label=label, emoji=emoji, custom_id=f"rr_{role_id}")
        self.role_id = role_id

    async def callback(self, interaction: discord.Interaction):
        guild = interaction.guild
        if not guild:
            await interaction.response.send_message("❌ サーバー内で実行してください。", ephemeral=True)
            return

        role = guild.get_role(self.role_id)
        if not role:
            await interaction.response.send_message("❌ 対象のロールが見つかりませんでした。", ephemeral=True)
            return

        member = interaction.user
        try:
            if role in member.roles:
                await member.remove_roles(role)
                await interaction.response.send_message(f"✅ ロール **{role.name}** を外しました。", ephemeral=True)
            else:
                await member.add_roles(role)
                await interaction.response.send_message(f"🎉 ロール **{role.name}** を付与しました！", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"⚠️ エラーが発生しました: {e}", ephemeral=True)

@bot.tree.command(name="reactionrole", description="最大6つのロールを同時に付与できるボタンパネルを設置します")
@app_commands.describe(
    title="パネルのタイトル", description="パネルの説明文",
    role1="1つ目のロール", label1="1つ目のボタン名", emoji1="1つ目の絵文字",
    role2="2つ目のロール", label2="2つ目のボタン名", emoji2="2つ目の絵文字",
    role3="3つ目のロール", label3="3つ目のボタン名", emoji3="3つ目の絵文字",
    role4="4つ目のロール", label4="4つ目のボタン名", emoji4="4つ目の絵文字",
    role5="5つ目のロール", label5="5つ目のボタン名", emoji5="5つ目の絵文字",
    role6="6つ目のロール", label6="6つ目のボタン名", emoji6="6つ目の絵文字"
)
async def reactionrole_command(
    interaction: discord.Interaction, 
    title: str = "📌 マルチロール取得パネル",
    description: str = "下のボタンを押して、それぞれのロールの取得・解除を行ってください。",
    role1: discord.Role = None, label1: str = None, emoji1: str = None,
    role2: discord.Role = None, label2: str = None, emoji2: str = None,
    role3: discord.Role = None, label3: str = None, emoji3: str = None,
    role4: discord.Role = None, label4: str = None, emoji4: str = None,
    role5: discord.Role = None, label5: str = None, emoji5: str = None,
    role6: discord.Role = None, label6: str = None, emoji6: str = None
):
    if not interaction.user.guild_permissions.manage_roles:
        await interaction.response.send_message("❌ このコマンドを実行するには「ロールの管理」権限が必要です。", ephemeral=True)
        return

    roles_info = [
        (role1, label1, emoji1), (role2, label2, emoji2), (role3, label3, emoji3),
        (role4, label4, emoji4), (role5, label5, emoji5), (role6, label6, emoji6)
    ]
    
    view = discord.ui.View(timeout=None)
    active_count = 0
    for role, label, emoji in roles_info:
        if role is not None:
            if label is None:
                await interaction.response.send_message(f"❌ ロール「{role.name}」に対してボタンのテキストが指定されていません。", ephemeral=True)
                return
            view.add_item(MultiReactionRoleButton(role_id=role.id, label=label, emoji=emoji))
            active_count += 1

    if active_count == 0:
        await interaction.response.send_message("❌ 最低1つのロールを指定してください。", ephemeral=True)
        return

    embed = discord.Embed(title=title, description=description, color=discord.Color.blue())
    await interaction.channel.send(embed=embed, view=view)
    await interaction.response.send_message("✅ ロールパネルを設置しました！", ephemeral=True)


# --- 9. 起動処理 ---
if __name__ == "__main__":
    web_thread = threading.Thread(target=run_web)
    web_thread.daemon = True
    web_thread.start()

    token = os.environ.get("DISCORD_TOKEN") or os.environ.get("BOT_TOKEN")
    if not token:
        print("❌ エラー: DISCORD_TOKEN が設定されていません。")
    else:
        bot.run(token)
