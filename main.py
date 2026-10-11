import os
import discord
from discord.ext import tasks
from datetime import datetime, time, timezone, timedelta
import feedparser
import requests
from keep_alive import keep_alive

# Intentsの設定
intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

# 日本時間のタイムゾーン (UTC+9)
JST = timezone(timedelta(hours=9))
UTC = timezone.utc

# 毎日朝8:00 (JST) = 前日 23:00 (UTC) に実行
# discord.ext.tasks では time に UTC を指定するのが確実です
NOTIFICATION_TIME = time(hour=11, minute=24, tzinfo=UTC)

# 送信先のチャンネルID（環境変数または直接指定）
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))

@client.event
async def on_ready():
    print(f'ログインしました: {client.user}')
    daily_task.start()

# ニュースを取得する関数（Googleニュース RSSを使用）
def get_latest_news():
    url = "https://news.google.com/rss?hl=ja&gl=JP&ceid=JP:ja"
    feed = feedparser.parse(url)
    news_list = []
    for entry in feed.entries[:10]:  # 最新10件
        news_list.append(f"・[{entry.title}]({entry.link})")
    return news_list

# 天気情報を取得する関数（例として気象庁APIやオープンデータなどを利用）
# 47都道府県すべてを個別に並べると文字数オーバーするため、地域ブロックごとや代表的な情報をまとめるアプローチがおすすめです
def get_weather_summary():
    # ※気象庁API等から47都道府県データを取得・整形する処理をここに記述
    # サンプルとして全47都道府県の簡易プレースホルダーを返す例
    prefectures = [
        "北海道", "青森県", "岩手県", "宮城県", "秋田県", "山形県", "福島県",
        "茨城県", "栃木県", "群馬県", "埼玉県", "千葉県", "東京都", "神奈川県",
        "新潟県", "富山県", "石川県", "福井県", "山梨県", "長野県", "岐阜県",
        "静岡県", "愛知県", "三重県", "滋賀県", "京都府", "大阪府", "兵庫県",
        "奈良県", "和歌山県", "鳥取県", "島根県", "岡山県", "広島県", "山口県",
        "徳島県", "香川県", "愛媛県", "高知県", "福岡県", "佐賀県", "長崎県",
        "熊本県", "大分県", "宮崎県", "鹿児島県", "沖縄県"
    ]
    
    # 実際には気象庁API (https://www.jma.go.jp/bosai/forecast/data/forecast/...) などから取得します
    # ここでは例として簡易文字列を作成
    weather_text = "☀️ **【47都道府県 本日の天気概況】**\n"
    # ※長すぎる場合は分割送信を推奨します
    weather_text += "全国各地の天気予報データを取得しました（詳細は気象庁HP等をご確認ください）。\n"
    return weather_text

@tasks.loop(time=NOTIFICATION_TIME)
async def daily_task():
    if CHANNEL_ID == 0:
        print("CHANNEL_IDが設定されていません。")
        return
        
    channel = client.get_channel(CHANNEL_ID)
    if channel is None:
        try:
            channel = await client.fetch_channel(CHANNEL_ID)
        except Exception as e:
            print(f"チャンネルの取得に失敗しました: {e}")
            return

    # 朝の挨拶と天気
    weather_msg = get_weather_summary()
    greeting = "おはようございます！✨ 本日の朝のニュースと全国の天気をお届けします。\n\n" + weather_msg
    await channel.send(greeting)

    # 最新ニュース10件
    news_list = get_latest_news()
    news_msg = "📰 **【最新ニュース 10選】**\n" + "\n".join(news_list)
    await channel.send(news_msg)

@daily_task.before_loop
async def before_daily_task():
    await client.wait_until_ready()

# Keep-Aliveサーバーを起動
keep_alive()

# Botの起動
TOKEN = os.getenv("DISCORD_TOKEN")
client.run(TOKEN)
