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
intents.members = True 

# 自動で「いいね」を付けたいチャンネルのIDを設定してください
# 環境変数から取得するか、直接チャンネルIDの数字を書き込んでください
TARGET_REACTION_CHANNEL_ID = int(os.environ.get("TARGET_REACTION_CHANNEL_ID", "0"))

class ReactionRolePersistentView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

class MyBot(commands.Bot):
    async def setup_hook(self):
        bot.add_view(ReactionRolePersistentView())

bot = MyBot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    try:
        synced = await bot.tree.sync()
        print(f"Successfully synced {len(synced)} command(s).")
    except Exception as e:
        print(f"Failed to sync commands: {e}")


# --- 自動イイネ（リアクション）機能 ---
@bot.event
async def on_message(message: discord.Message):
    # ボット自身のメッセージや、サーバー外（DMなど）は無視する
    if message.author.bot or not message.guild:
        return

    # 指定されたチャンネルIDと一致する場合のみリアクションを付与
    # TARGET_REACTION_CHANNEL_ID が 0 の場合は機能しません
    if TARGET_REACTION_CHANNEL_ID != 0 and message.channel.id == TARGET_REACTION_CHANNEL_ID:
        try:
            # 「👍」の絵文字を自動で追加する
            await message.add_reaction("👍")
        except discord.Forbidden:
            print("❌ リアクションを追加する権限がありません（「リアクションの追加」権限を確認してください）。")
        except Exception as e:
            print(f"⚠️ リアクション付与中にエラーが発生しました: {e}")

    # これがないと他のコマンド（/ipや/zipなど）が反応しなくなるため必須です
    await bot.process_commands(message)


# --- 3. IPアドレス検索コマンド ---
@bot.tree.command(name="ip", description="指定されたIPアドレスの国、都市、プロバイダ、住所情報を検索します")
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
            await interaction.followup.send(f"⚠️ 情報を取得できませんでした。理由: {data.get('message')}")
            return

        zip_code = data.get("zip", "").strip().replace("-", "").replace(" ", "")
        full_address = "不明または日本国外"
        
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
        await interaction.followup.send(embed=embed)
    except Exception as e:
        await interaction.followup.send(f"⚠️ エラーが発生しました: {e}")


# --- 4. 郵便番号検索コマンド ---
@bot.tree.command(name="zip", description="日本の郵便番号から住所を検索します")
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
        full_address = f"{res.get('address1', '')}{res.get('address2', '')}{res.get('address3', '')}"
        
        embed = discord.Embed(title=f"📮 郵便番号検索結果: 〒{clean_zip[:3]}-{clean_zip[3:]}", color=discord.Color.green())
        embed.add_field(name="📍 住所", value=full_address, inline=False)
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


# --- 6. URL安全チェックコマンド ---
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


# --- 7. 複数ロール対応リアクションロール設置コマンド ---
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
        except discord.Forbidden:
            await interaction.response.send_message("❌ ボットの権限（ロールの管理）または階調が不足しています。", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"⚠️ エラーが発生しました: {e}", ephemeral=True)

@bot.tree.command(name="reactionrole", description="最大3つのロールを同時に付与できるボタンパネルを設置します")
@app_commands.describe(
    role1="1つ目のロール", label1="1つ目のボタン名", emoji1="1つ目の絵文字（任意）",
    role2="2つ目のロール（任意）", label2="2つ目のボタン名（任意）", emoji2="2つ目の絵文字（任意）",
    role3="3つ目のロール（任意）", label3="3つ目のボタン名（任意）", emoji3="3つ目の絵文字（任意）"
)
async def reactionrole_command(
    interaction: discord.Interaction, 
    role1: discord.Role, label1: str, emoji1: str = None,
    role2: discord.Role = None, label2: str = None, emoji2: str = None,
    role3: discord.Role = None, label3: str = None, emoji3: str = None
):
    if not interaction.user.guild_permissions.manage_roles:
        await interaction.response.send_message("❌ このコマンドを実行するには「ロールの管理」権限が必要です。", ephemeral=True)
        return

    selected_roles = []
    roles_info = [(role1, label1, emoji1), (role2, label2, emoji2), (role3, label3, emoji3)]

    for role, label, emoji in roles_info:
        if role is not None:
            if label is None:
                await interaction.response.send_message(f"❌ ロール「{role.name}」に対してボタンのテキスト（label）が指定されていません。", ephemeral=True)
                return
            if role.id in selected_roles:
                await interaction.response.send_message(f"❌ 同じロール（{role.name}）を複数選択することはできません。", ephemeral=True)
                return
            if role.position >= interaction.guild.me.top_role.position:
                await interaction.response.send_message(f"❌ ロール「{role.name}」は私のロールより権限が上のため付与できません。", ephemeral=True)
                return
            
            selected_roles.append(role.id)

    view = discord.ui.View(timeout=None)
    for role, label, emoji in roles_info:
        if role is not None:
            view.add_item(MultiReactionRoleButton(role_id=role.id, label=label, emoji=emoji))

    embed = discord.Embed(
        title="📌 マルチロール取得パネル",
        description="下のボタンを押して、それぞれのロールの取得・解除を行ってください。",
        color=discord.Color.blue()
    )

    await interaction.channel.send(embed=embed, view=view)
    await interaction.response.send_message("✅ 複数ロール対応のパネルを設置しました！", ephemeral=True)


# --- 8. 起動処理 ---
if __name__ == "__main__":
    web_thread = threading.Thread(target=run_web)
    web_thread.daemon = True
    web_thread.start()

    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        print("❌ エラー: DISCORD_TOKEN が設定されていません。")
    else:
        bot.run(token)
