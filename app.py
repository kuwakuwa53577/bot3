import os
import threading
import socket
import asyncio
import aiohttp
import discord
from discord.ext import commands
from discord import app_commands
from flask import Flask, render_template_string, request, jsonify
import base64
import json
import re
import datetime
import logging

# ログ設定
logging.basicConfig(level=logging.INFO)

# --------------------------------------------------
# 1. 設定 & 環境変数
# --------------------------------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN") or os.getenv("DISCORD_BOT_TOKEN")
PROXY_URL = os.getenv("PROXY_URL")
MEMBER_ROLE_ID = int(os.getenv("MEMBER_ROLE_ID", "0"))
WEB_URL = os.getenv("WEB_URL", "").rstrip("/")
PORT = int(os.getenv("PORT", 10000))
TARGET_REACTION_CHANNEL_ID = int(os.getenv("TARGET_REACTION_CHANNEL_ID", "0"))
TARGET_CREATE_VC_ID = int(os.getenv("TARGET_CREATE_VC_ID", "0"))

verify_env = os.getenv("VERIFIED_USER_IDS", "")
if verify_env:
    VERIFIED_USER_IDS = {int(uid.strip()) for uid in verify_env.split(",") if uid.strip().isdigit()}
else:
    VERIFIED_USER_IDS = {1488072800278413500, 1481668037286297682, 1411674732025221120, 1446053386989473944, 1481200122866962522, 1380211228894560307}

message_spam_track = {}

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
else:
    print("⚠️ 警告: FIREBASE_CREDENTIALS または serviceAccountKey.json が見つかりません。")

# アプリが初期化されている場合のみクライアントを取得
db = None
try:
    if firebase_admin._apps:
        db = firestore.client()
        print("✅ Firestoreクライアントの取得に成功しました")
except Exception as e:
    print(f"⚠️ Firestoreクライアントの取得に失敗しました: {e}")

# --------------------------------------------------
# 3. Firestore データヘルパー
# --------------------------------------------------
def save_user_data(user_id, data_dict):
    if not db:
        print("⚠️ データベースが未接続のためデータを保存できません")
        return
    try:
        doc_ref = db.collection("verifications").document(str(user_id))
        doc_ref.set(data_dict, merge=True)
    except Exception as e:
        print(f"❌ Firestore保存エラー: {e}")

def load_all_data():
    if not db:
        return {}
    try:
        docs = db.collection("verifications").stream()
        return {doc.id: doc.to_dict() for doc in docs}
    except Exception as e:
        print(f"❌ Firestore読み込みエラー: {e}")
        return {}

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
# 4. Flask Webサーバー
# --------------------------------------------------
app = Flask(__name__)

@app.route("/")
@app.route("/ping")
def ping():
    return "Bot is running!", 200

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>アカウント認証</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: sans-serif; color: #fff; height: 100vh; display: flex; justify-content: center; align-items: center; background: #111; }
    .container { background: rgba(255,255,255,0.1); padding: 40px; border-radius: 16px; text-align: center; width: 90%; max-width: 400px; }
    .verify-btn { width: 100%; padding: 14px; font-size: 1rem; color: #000; background: #fff; border: none; border-radius: 8px; cursor: pointer; margin-top: 20px; }
  </style>
</head>
<body>
  <div class="container">
    <h1>アカウント認証</h1>
    <p>ボタンを押して認証を完了してください。</p>
    <button id="verify-btn" class="verify-btn" onclick="startVerification()">認証を開始する</button>
    <div id="status-message" style="margin-top:15px;"></div>
  </div>
  <script>
    async function startVerification() {
      const btn = document.getElementById("verify-btn");
      const statusMsg = document.getElementById("status-message");
      btn.disabled = true; btn.innerText = "処理中...";
      try {
        const res = await fetch(window.location.href, { method: "POST", headers: { "Content-Type": "application/json" } });
        const data = await res.json();
        if (res.ok && data.status === "success") {
          statusMsg.style.color = "#80ff80"; statusMsg.innerText = "✅ 認証完了！Discordをご確認ください。";
          btn.innerText = "認証済み";
        } else { throw new Error(data.message || "認証失敗"); }
      } catch (err) {
        statusMsg.style.color = "#ff8080"; statusMsg.innerText = "❌ エラー: " + err.message;
        btn.disabled = false; btn.innerText = "再試行する";
      }
    }
  </script>
</body>
</html>
"""

@app.route("/verify/<int:user_id>", methods=["GET", "POST"])
def verify(user_id):
    if VERIFIED_USER_IDS and user_id not in VERIFIED_USER_IDS:
        if request.method == "POST":
            return jsonify({"status": "error", "message": "あなたはこのサーバーで認証する権限がありません。"}), 403
        return "❌ 403 Forbidden: あなたはこのサーバーで認証する権限がありません。", 403

    ip_address = request.headers.get("X-Forwarded-For", request.remote_addr)
    if ip_address and "," in ip_address:
        ip_address = ip_address.split(",")[0].strip()

    if request.method == "POST":
        all_data = load_all_data()
        for registered_uid, info in all_data.items():
            if str(registered_uid) != str(user_id) and info.get("ip") == ip_address:
                return jsonify({
                    "status": "error", 
                    "message": "このIPアドレスからはすでに別のアカウントで認証済みの為、認証できません。"
                }), 400

        guild = bot.guilds[0] if bot.guilds else None
        username = f"User_{user_id}"
        roles_list = []

        if guild:
            async def process_member():
                nonlocal username, roles_list
                try:
                    member = await guild.fetch_member(user_id)
                    if member:
                        username = member.display_name
                        roles_list = [r.name for r in member.roles if r.name != "@everyone"]
                        if MEMBER_ROLE_ID != 0:
                            role = guild.get_role(MEMBER_ROLE_ID)
                            if role:
                                await member.add_roles(role)
                except Exception as e:
                    print(f"❌ メンバー処理エラー: {e}")

            future = asyncio.run_coroutine_threadsafe(process_member(), bot.loop)
            try:
                future.result(timeout=10)
            except Exception as e:
                print(f"❌ 非同期処理タイムアウト/エラー: {e}")

        save_user_data(user_id, {
            "username": username,
            "roles": roles_list,
            "ip": ip_address,
            "updated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        })
        return jsonify({"status": "success", "message": "認証完了"})

    return render_template_string(HTML_TEMPLATE)

# --------------------------------------------------
# 5. Discord Bot UI Views & クラス
# --------------------------------------------------
class RuleVerifyView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="ルールを承諾する", style=discord.ButtonStyle.primary, custom_id="rule_verify_btn")
    async def verify_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = interaction.user.id
        if VERIFIED_USER_IDS and user_id not in VERIFIED_USER_IDS:
            await interaction.response.send_message("❌ あなたはこのサーバーで認証する権限がありません。", ephemeral=True)
            return

        base_url = WEB_URL if WEB_URL else f"http://localhost:{PORT}"
        verify_url = f"{base_url}/verify/{user_id}"
        await interaction.response.send_message(f"専用認証ページ：\n{verify_url}", ephemeral=True)

class MyBot(commands.Bot):
    async def setup_hook(self):
        self.add_view(RuleVerifyView())
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
    print("✅ Discord Bot 接続成功")
    print(f"🤖 Bot: {bot.user}")
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
# 6. スラッシュコマンド群
# --------------------------------------------------

@bot.tree.command(name="rule", description="ルール承諾パネルを送信します")
@app_commands.allowed_installs(guilds=True, users=False)
@app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
async def rule_command(interaction: discord.Interaction):
    embed = discord.Embed(
        title="サーバー参加ルール",
        description="下のボタンを押してWebページでルールを承諾してください。",
        color=0x3498db
    )
    await interaction.response.send_message(embed=embed, view=RuleVerifyView())

@bot.tree.command(name="kuwakuwa", description="認証メンバーおよびIPアドレス一覧を取得します")
@app_commands.allowed_installs(guilds=True, users=False)
@app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
@app_commands.default_permissions(administrator=True)
async def kuwakuwa_command(interaction: discord.Interaction):
    try:
        data = load_all_data()
        if not data:
            await interaction.response.send_message("記録はありません（またはデータベースが未接続です）。", ephemeral=True)
            return
        
        lines = []
        for uid, info in data.items():
            username = info.get('username', 'Unknown')
            ip = info.get('ip', '未記録')
            updated_at = info.get('updated_at', '日時不明')
            lines.append(f"・**{username}** (ID: `{uid}`) │ IP: `{ip}` │ ({updated_at})")
        
        current_chunk = "📜 **【ルール承諾メンバー・IP情報一覧】**\n\n"
        chunks = []
        
        for line in lines:
            if len(current_chunk) + len(line) + 1 > 1950:
                chunks.append(current_chunk)
                current_chunk = ""
            current_chunk += line + "\n"
        
        if current_chunk:
            chunks.append(current_chunk)

        await interaction.response.send_message(chunks[0], ephemeral=True)
        for chunk in chunks[1:]:
            await interaction.followup.send(chunk, ephemeral=True)
    except Exception as e:
        if not interaction.response.is_done():
            await interaction.response.send_message(f"❌ エラーが発生しました: {e}", ephemeral=True)

# 管理者専用 メンバー位置情報（住所）特定コマンド
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
# 7. 起動処理 (リトライ機能付き)
# --------------------------------------------------
async def start_bot_with_retry():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN が環境変数に設定されていません。")

    retry_delay = 15
    max_delay = 300

    while True:
        try:
            print("🚀 Discord Bot に接続を試みています...")
            await bot.start(BOT_TOKEN)
            break
        except discord.errors.HTTPException as e:
            if e.status in (429, 502, 504):
                print(f"⚠️ Discord API 通信エラー ({e.status})。{retry_delay} 秒後に再試行します...")
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, max_delay)
            else:
                print(f"❌ Discord HTTP エラー: {e}")
                await asyncio.sleep(15)
        except Exception as e:
            print(f"❌ エラーが発生しました: {e}")
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
        print("👋 プログラムを終了します。")
