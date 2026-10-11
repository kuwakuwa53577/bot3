import os
import discord
from discord.ext import tasks
from datetime import datetime, time, timezone, timedelta
import feedparser
from keep_alive import keep_alive

# Intentsの設定
intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

# 日本時間のタイムゾーン (UTC+9)
JST = timezone(timedelta(hours=9))
UTC = timezone.utc

# 毎日朝8:00 (JST) = 前日 23:00 (UTC) に実行
NOTIFICATION_TIME = time(hour=11, minute=29, tzinfo=UTC)

# 送信先のチャンネルID
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))

@client.event
async def on_ready():
    print(f'ログインしました: {client.user}')
    if not daily_task.is_running():
        daily_task.start()

# 最新ニュース10件を取得する関数
def get_latest_news():
    try:
        url = "https://news.google.com/rss?hl=ja&gl=JP&ceid=JP:ja"
        feed = feedparser.parse(url)
        news_list = []
        for entry in feed.entries[:10]:
            news_list.append(f"・[{entry.title}]({entry.link})")
        return news_list
    except Exception as e:
        print(f"ニュース取得エラー: {e}")
        return []

# 天気情報を取得する関数（サンプル）
def get_weather_summary():
    # 47都道府県の簡易まとめ（必要に応じてJMA API等に拡張可能）
    weather_text = "☀️ **【全国 47都道府県の天気概況】**\n"
    weather_text += "本日は全国的に各地の気象情報をご確認の上、お出かけください。詳細は気象庁の最新予報をご参照ください。\n"
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

    try:
        # 1. 天気の送信
        weather_msg = get_weather_summary()
        greeting = "おはようございます！✨ 本日の朝のニュースと全国の天気をお届けします。\n\n" + weather_msg
        await channel.send(greeting)

        # 2. ニュース10件の取得と送信
        news_list = get_latest_news()
        if news_list:
            news_msg = "📰 **【最新ニュース 10選】**\n" + "\n".join(news_list)
            await channel.send(news_msg)
        else:
            await channel.send("📰 ニュースの取得に失敗しました。")
            
        print("朝の定期送信が完了しました。")
    except Exception as e:
        print(f"メッセージ送信中にエラーが発生しました: {e}")

@daily_task.before_loop
async def before_daily_task():
    await client.wait_until_ready()

# Keep-Aliveサーバーを起動
keep_alive()

# Botの起動
TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    print("エラー: DISCORD_TOKENが設定されていません。")
else:
    client.run(TOKEN)
