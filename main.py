import discord
from discord.ext import commands
from discord import app_commands
import aiohttp
import asyncio
from urllib.parse import urlencode
import os
import json
import time
import random
from datetime import datetime, timedelta

# ランダムカラー選択用の関数
def get_random_color():
    """指定された5色からランダムで1色を選択"""
    colors = [0x808080, 0xFFFFCC, 0xFFFF00, 0xCCCC33, 0xCCFFCC]
    return random.choice(colors)
BOT_TOKEN = os.getenv('DISCORD_BOT_TOKEN')
CLIENT_ID = os.getenv('DISCORD_CLIENT_ID')
CLIENT_SECRET = os.getenv('DISCORD_CLIENT_SECRET')
DISCORD_REDIRECT_URI = os.getenv('DISCORD_REDIRECT_URI')
RENDER_EXTERNAL_URL = os.getenv('RENDER_EXTERNAL_URL')

# Render環境での設定
if RENDER_EXTERNAL_URL:
    BASE_URL = RENDER_EXTERNAL_URL
    REDIRECT_URI = f"{RENDER_EXTERNAL_URL}/callback"
elif DISCORD_REDIRECT_URI:
    REDIRECT_URI = DISCORD_REDIRECT_URI
    BASE_URL = REDIRECT_URI.replace('/callback', '')
else:
    REDIRECT_URI = 'http://0.0.0.0:10000/callback'
    BASE_URL = "http://0.0.0.0:10000"
OAUTH_URL_BASE = 'https://discord.com/api/oauth2/authorize'
TOKEN_URL = 'https://discord.com/api/oauth2/token'
USER_URL = 'https://discord.com/api/users/@me'
GUILD_MEMBER_URL = 'https://discord.com/api/guilds/{}/members/{}'

class OAuthBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = False  # Privileged intentを無効化
        intents.guilds = True
        super().__init__(command_prefix='/', intents=intents)

        # サーバーごとの設定を管理
        self.guild_configs = {}

        # OAuth認証待ちのユーザーを追跡
        self.pending_auths = {}

        # 認証済みユーザーを保存（guild_id: [user_ids]）
        self.authenticated_users = {}

        # ユーザーのアクセストークンを保存（user_id: access_token）
        self.user_tokens = {}

        # ユーザーレベルシステム（guild_id: {user_id: {"level": int, "xp": int, "message_count": int}}）
        self.user_levels = {}

        # サーバー参加日時を記録（guild_id: timestamp）
        self.guild_join_dates = {}

        # 使用済み（2週間制限で退出済み）のサーバーを記録
        self.expired_guilds = set()  # 再度招待できないサーバーIDのセット

        # 定期削除タイマーを管理
        self.scheduled_nukes = {}  # {channel_id: asyncio.Task}

        # 半自動販売機システム（サーバーごと）
        self.vending_machines = {}  # {guild_id: {'products': {}, 'orders': {}, 'admin_channels': set(), 'next_order_id': 1}}

    async def on_ready(self):
        print(f'{self.user} がログインしました！')
        print(f'参加しているサーバー: {len(self.guilds)}個')
        print(f'RENDER_EXTERNAL_URL: {RENDER_EXTERNAL_URL}')
        print(f'BASE_URL: {BASE_URL}')
        print(f'REDIRECT_URI: {REDIRECT_URI}')

        # 参加している全サーバーの情報を表示
        for guild in self.guilds:
            print(f'- {guild.name} (ID: {guild.id})')
            # サーバーの設定を初期化（まだ設定されていない場合）
            if guild.id not in self.guild_configs:
                self.guild_configs[guild.id] = {
                    'default_role_id': None,
                    'authorized_channels': []
                }

            # 参加日時を記録（既に記録されていない場合のみ）
            if guild.id not in self.guild_join_dates:
                self.guild_join_dates[guild.id] = time.time()
                print(f'サーバー {guild.name} にさんかしたお！')

        # プレイ中ステータスを設定
        await self.update_status()

        # スラッシュコマンドを同期
        try:
            synced = await self.tree.sync()
            print(f'{len(synced)}個のスラッシュコマンドを同期しました')
        except Exception as e:
            print(f'スラッシュコマンドの同期エラー: {e}')

        # 2週間制限を無効化（コメントアウト）
        # asyncio.create_task(self.check_guild_expiry())

        # Webサーバーを開始
        await self.start_web_server()

    async def update_status(self):
        """プレイ中ステータスを更新"""
        try:
            guild_count = len(self.guilds)
            activity = discord.Game(name=f"{guild_count}個のサーバーで動作中なう")
            await self.change_presence(activity=activity, status=discord.Status.online)
            print(f'ステータスを更新: {guild_count}個のサーバーで動作中なう')
        except Exception as e:
            print(f'ステータス更新エラー: {e}')

    async def check_guild_expiry(self):
        """2週間制限をチェックして期限切れのサーバーから退出"""
        while True:
            try:
                current_time = time.time()
                two_weeks = 14 * 24 * 60 * 60  # 2週間（秒）

                expired_guilds = []
                for guild_id, join_time in list(self.guild_join_dates.items()):
                    if current_time - join_time >= two_weeks:
                        guild = self.get_guild(guild_id)
                        if guild:
                            expired_guilds.append(guild)

                for guild in expired_guilds:
                    try:
                        # 退出前に通知を送信（可能であれば）
                        try:
                            # システムチャンネルまたは最初のテキストチャンネルに通知
                            notification_channel = guild.system_channel
                            if not notification_channel:
                                for channel in guild.text_channels:
                                    if channel.permissions_for(guild.me).send_messages:
                                        notification_channel = channel
                                        break

                            if notification_channel:
                                expire_embed = discord.Embed(
                                    title="ぼっとについてお知らせ",
                                    description="無料期間の2週間がしゅうりょうしました！。\n"
                                               "引き続きご利用をご希望の場合は、再度招待してください。\n\n"
                                               "ご利用いただき、ありがとうございました！",
                                    color=get_random_color(),
                                    timestamp=discord.utils.utcnow()
                                )
                                await notification_channel.send(embed=expire_embed)
                        except Exception as e:
                            print(f'退出通知送信エラー (Guild {guild.name}): {e}')

                        # サーバーから退出
                        await guild.leave()
                        print(f'✅ 2週間制限により {guild.name} から退出しました')

                        # 使用済みサーバーとして記録（再招待を防ぐため）
                        self.expired_guilds.add(guild.id)
                        print(f'📝 サーバー {guild.name} (ID: {guild.id}) を使用済みリストに追加しました')

                        # データをクリーンアップ
                        if guild.id in self.guild_join_dates:
                            del self.guild_join_dates[guild.id]
                        if guild.id in self.guild_configs:
                            del self.guild_configs[guild.id]
                        if guild.id in self.authenticated_users:
                            del self.authenticated_users[guild.id]
                        if guild.id in self.user_levels:
                            del self.user_levels[guild.id]

                    except Exception as e:
                        print(f'サーバー退出エラー ({guild.name}): {e}')

                # ステータスを更新
                if expired_guilds:
                    await self.update_status()

                # 1時間ごとにチェック
                await asyncio.sleep(3600)

            except Exception as e:
                print(f'期限チェックエラー: {e}')
                await asyncio.sleep(3600)  # エラーが発生しても1時間後に再試行

    async def on_guild_join(self, guild):
        """新しいサーバーに参加した時の処理"""
        print(f'新しいサーバーに参加しました: {guild.name} (ID: {guild.id})')

        # 期限制限を無効化（すべてのサーバーを受け入れ）

        self.guild_configs[guild.id] = {
            'default_role_id': None,
            'authorized_channels': []
        }

        # 参加日時を記録
        self.guild_join_dates[guild.id] = time.time()
        print(f'サーバー {guild.name} の参加日時を記録しました')

        # ステータスを更新
        await self.update_status()

        # 歓迎メッセージを送信
        try:
            # システムチャンネルまたは最初のテキストチャンネルを探す
            welcome_channel = guild.system_channel
            if not welcome_channel:
                for channel in guild.text_channels:
                    if channel.permissions_for(guild.me).send_messages:
                        welcome_channel = channel
                        break

            if welcome_channel:
                welcome_embed = discord.Embed(
                    title="m.m.VDを追加くださり、ありがとうございます！",
                    description=f"機能を簡単に説明します！\n\n"
                               "当Botは以下の機能を提供します：\n"
                               "• 半自動販売機\n"
                               "• レベル機能\n"
                               "• nukeとかその他もろもろ\n"
                               "• マスカレードのlog\n"
                               "• あとは自分でhelpコマンドで確認してね！\n\n",
                    color=get_random_color(),
                    timestamp=discord.utils.utcnow()
                )

                welcome_embed.add_field(
                    name="設定方法",
                    value="管理者は `/role` コマンドで認証システムを設定できます",
                    inline=True
                )

                await welcome_channel.send(embed=welcome_embed)
                print(f'認証完了メッセージを {guild.name} に送信しました')

        except Exception as e:
            print(f'歓迎メッセージ送信エラー ({guild.name}): {e}')

    async def on_guild_remove(self, guild):
        """サーバーから退出した時の処理"""
        print(f'サーバーから退出したよ！: {guild.name} (ID: {guild.id})')

        # 関連データをクリーンアップ
        if guild.id in self.guild_configs:
            del self.guild_configs[guild.id]
        if guild.id in self.guild_join_dates:
            del self.guild_join_dates[guild.id]
        if guild.id in self.authenticated_users:
            del self.authenticated_users[guild.id]
        if guild.id in self.user_levels:
            del self.user_levels[guild.id]
        if guild.id in self.vending_machines:
            del self.vending_machines[guild.id]

        # ステータスを更新
        await self.update_status()

    async def on_message(self, message):
        """メッセージが送信された時の処理"""
        # Botのメッセージは無視
        if message.author.bot:
            return

        # DMは無視
        if not message.guild:
            return

        guild_id = message.guild.id
        user_id = str(message.author.id)

        # XPを追加（1メッセージにつき1XP）
        leveled_up, old_level, new_level = self.add_xp(guild_id, user_id, 1)

        # レベルアップした場合は通知
        if leveled_up:
            level_up_embed = discord.Embed(
                title="🎉 レベルアップ！",
                description=f"{message.author.mention} mpレベルが {new_level} になりました！",
                color=get_random_color()
            )
            level_up_embed.add_field(
                name="さっきまでのレベル",
                value=f"レベル {old_level}",
                inline=True
            )
            level_up_embed.add_field(
                name="レベルアップ時のレベル", 
                value=f"レベル {new_level}",
                inline=True
            )
            level_up_embed.set_thumbnail(url=message.author.display_avatar.url)

            await message.channel.send(embed=level_up_embed)

        # プレフィックスコマンドの処理
        await self.process_commands(message)

    def get_guild_config(self, guild_id):
        """サーバーの設定を取得"""
        return self.guild_configs.get(guild_id, {
            'default_role_id': None,
            'authorized_channels': []
        })

    def set_guild_config(self, guild_id, config):
        """サーバーの設定を保存"""
        self.guild_configs[guild_id] = config

    def get_guild_vending_machine(self, guild_id):
        """サーバーの販売機データを取得（存在しない場合は初期化）"""
        if guild_id not in self.vending_machines:
            self.vending_machines[guild_id] = {
                'products': {},  # {product_id: {'name': str, 'price': int, 'description': str, 'stock': int, 'inventory': [str]}}
                'orders': {},    # {order_id: {'user_id': str, 'product_id': str, 'status': str, 'channel_id': int}}
                'admin_channels': set(),  # 管理者チャンネルのIDセット
                'achievement_channel': None,  # 実績チャンネルのID
                'next_order_id': 1
            }
        return self.vending_machines[guild_id]



    def calculate_level_from_xp(self, xp):
        """XPからレベルを計算（100XPごとに1レベルアップ）"""
        return int(xp // 100) + 1

    def calculate_xp_for_level(self, level):
        """指定レベルに必要なXPを計算"""
        return (level - 1) * 100

    def add_xp(self, guild_id, user_id, xp_amount=1):
        """ユーザーにXPを追加し、レベルアップをチェック"""
        if guild_id not in self.user_levels:
            self.user_levels[guild_id] = {}

        if user_id not in self.user_levels[guild_id]:
            self.user_levels[guild_id][user_id] = {
                "level": 1,
                "xp": 0,
                "message_count": 0
            }

        user_data = self.user_levels[guild_id][user_id]
        old_level = user_data["level"]

        # XPとメッセージカウントを追加
        user_data["xp"] += xp_amount
        user_data["message_count"] += 1

        # 新しいレベルを計算
        new_level = self.calculate_level_from_xp(user_data["xp"])
        user_data["level"] = new_level

        # レベルアップした場合はTrueを返す
        return new_level > old_level, old_level, new_level

    async def start_web_server(self):
        from aiohttp import web

        app = web.Application()
        app.router.add_get('/auth', self.handle_auth_request)
        app.router.add_get('/callback', self.handle_oauth_callback)

        runner = web.AppRunner(app)
        await runner.setup()

        # Renderではポート10000を使用
        port = int(os.getenv('PORT', 10000))
        site = web.TCPSite(runner, '0.0.0.0', port)
        await site.start()
        print(f'Webサーバーが http://0.0.0.0:{port} で開始されました')

    async def handle_auth_request(self, request):
        # ロール情報とサーバー情報を取得
        role_id = request.query.get('role_id')
        guild_id = request.query.get('guild_id')
        role_name = request.query.get('role_name', '指定されたロール')

        if not role_id or not guild_id:
            return web.Response(text='ロール情報またはサーバー情報が指定されていません', status=400)

        # OAuth認証URLを生成
        params = {
            'client_id': CLIENT_ID,
            'redirect_uri': REDIRECT_URI,
            'response_type': 'code',
            'scope': 'identify guilds.join',
            'state': f'discord_oauth_{guild_id}_{role_id}'
        }

        auth_url = f"{OAUTH_URL_BASE}?{urlencode(params)}"

        html = f'''
        <!DOCTYPE html>
        <html>
        <head>
            <title>Discord OAuth認証</title>
            <style>
                body {{ font-family: Arial, sans-serif; text-align: center; margin-top: 100px; }}
                .btn {{ background: #5865F2; color: white; padding: 15px 30px; 
                        text-decoration: none; border-radius: 5px; font-size: 18px; }}
                .btn:hover {{ background: #4752C4; }}
            </style>
        </head>
        <body>
            <h1>Discord認証</h1>
            <p>以下のボタンをクリックしてDiscordで認証してください</p>
            <p>付与されるロール: <strong>{role_name}</strong></p>
            <a href="{auth_url}" class="btn">Discordで認証</a>
        </body>
        </html>
        '''

        return web.Response(text=html, content_type='text/html')

    async def handle_oauth_callback(self, request):
        from aiohttp import web

        code = request.query.get('code')
        error = request.query.get('error')

        if error:
            return web.Response(text=f'認証エラー: {error}', status=400)

        if not code:
            return web.Response(text='認証コードが見つかりません', status=400)

        try:
            # アクセストークンを取得
            token_data = await self.get_access_token(code)
            access_token = token_data['access_token']

            # ユーザー情報を取得
            user_data = await self.get_user_info(access_token)
            user_id = user_data['id']
            username = user_data['username']

            # アクセストークンを保存
            self.user_tokens[user_id] = access_token
            print(f'💾 ユーザー {username} のアクセストークンをメモリに保存しました')

            # stateからサーバーIDとロールIDを取得
            state = request.query.get('state', '')
            guild_id = None
            role_id = None

            if state.startswith('discord_oauth_'):
                parts = state.replace('discord_oauth_', '').split('_')
                if len(parts) >= 2:
                    guild_id = int(parts[0])
                    role_id = int(parts[1])

            if not guild_id or not role_id:
                return web.Response(text='サーバー情報またはロール情報が見つかりません', status=400)

            # サーバーにメンバーを追加
            print(f'🔄 ユーザー {username} (ID: {user_id}) をサーバー {guild_id} に追加を試行中...')
            success = await self.add_member_to_guild(access_token, user_id, guild_id)

            if success:
                print(f'サーバーへの追加が成功しました')

                # サーバー参加の確認を複数回試行
                guild = self.get_guild(guild_id)
                member_found = False

                if guild:
                    for attempt in range(5):  # 最大5回試行
                        try:
                            # メンバーを直接フェッチしてキャッシュに追加
                            member = await guild.fetch_member(int(user_id))
                            print(f'👤 メンバーキャッシュに追加: {member.display_name} ({member.name})')
                            member_found = True
                            break
                        except discord.NotFound:
                            print(f'⚠️ 試行 {attempt + 1}/5: メンバー {user_id} がサーバー {guild.name} で見つかりません')
                            if attempt < 4:  # 最後の試行でない場合のみ待機
                                await asyncio.sleep(2)  # 2秒待機
                        except Exception as e:
                            print(f'❌ メンバーフェッチエラー: {e}')
                            if attempt < 4:
                                await asyncio.sleep(2)

                # メンバーが確認できた場合のみ認証済みユーザーとして記録
                if member_found:
                    # 指定されたロールを付与
                    print(f'ロール付与を試行中...')
                    role_assigned = await self.assign_role(user_id, guild_id, role_id)

                    # 認証済みユーザーとして記録
                    if guild_id not in self.authenticated_users:
                        self.authenticated_users[guild_id] = []
                    if user_id not in self.authenticated_users[guild_id]:
                        self.authenticated_users[guild_id].append(user_id)
                        print(f'認証済みユーザーに追加: {username} (User ID: {user_id}, Guild ID: {guild_id})')
                else:
                    print(f'サーバー参加の確認に失敗しました')
                    success = False  # 実際にはサーバー参加に失敗

                # ロール名を取得して表示
                guild = self.get_guild(guild_id)
                role = guild.get_role(role_id) if guild else None
                role_name = role.name if role else "指定されたロール"
                guild_name = guild.name if guild else "サーバー"

                if role_assigned:
                    html = f'''
                    <!DOCTYPE html>
                    <html>
                    <head>
                        <title>認証完了</title>
                        <style>
                            body {{ font-family: Arial, sans-serif; text-align: center; margin-top: 100px; }}
                            .success {{ color: #28a745; }}
                        </style>
                    </head>
                    <body>
                        <h1 class="success">認証完了！</h1>
                        <p>ようこそ {username} さん！</p>
                        <p>サーバー「{guild_name}」に参加し、ロール「{role_name}」が付与されました。</p>
                    </body>
                    </html>
                    '''
                else:
                    html = f'''
                    <!DOCTYPE html>
                    <html>
                    <head>
                        <title>認証完了</title>
                        <style>
                            body {{ font-family: Arial, sans-serif; text-align: center; margin-top: 100px; }}
                            .warning {{ color: #ffc107; }}
                        </style>
                    </head>
                    <body>
                        <h1 class="warning">部分的に完了</h1>
                        <p>ようこそ {username} さん！</p>
                        <p>サーバー「{guild_name}」に参加しましたが、ロールの付与に問題が発生しました。</p>
                        <p>管理者にお問い合わせください。</p>
                    </body>
                    </html>
                    '''
                return web.Response(text=html, content_type='text/html')
            else:
                # サーバーへの参加に失敗した場合
                guild = self.get_guild(guild_id)
                guild_name = guild.name if guild else "サーバー"

                html = f'''
                <!DOCTYPE html>
                <html>
                <head>
                    <title>参加失敗</title>
                    <style>
                        body {{ font-family: Arial, sans-serif; text-align: center; margin-top: 100px; }}
                        .error {{ color: #dc3545; }}
                        .info {{ color: #6c757d; margin-top: 20px; }}
                    </style>
                </head>
                <body>
                    <h1 class="error">サーバー参加に失敗</h1>
                    <p>申し訳ございません、{username} さん。</p>
                    <p>サーバー「{guild_name}」への参加に失敗しました。</p>
                    <div class="info">
                        <p>考えられる原因：</p>
                        <ul style="text-align: left; display: inline-block;">
                            <li>サーバーが満員です</li>
                            <li>サーバーの招待設定により参加が制限されています</li>
                            <li>一時的なエラーが発生しました</li>
                        </ul>
                        <p>しばらく時間をおいて再度お試しいただくか、サーバー管理者にお問い合わせください。</p>
                    </div>
                </body>
                </html>
                '''
                return web.Response(text=html, content_type='text/html', status=400)

        except Exception as e:
            print(f'処理エラーだよ！: {e}')
            return web.Response(text=f'処理中にエラーが発生しました、管理者に伝えてね: {e}', status=500)

    async def get_access_token(self, code):
        """認証コードからアクセストークンを取得"""
        data = {
            'client_id': CLIENT_ID,
            'client_secret': CLIENT_SECRET,
            'grant_type': 'authorization_code',
            'code': code,
            'redirect_uri': REDIRECT_URI
        }

        # レート制限対策でリトライ
        for attempt in range(3):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(TOKEN_URL, data=data) as response:
                        if response.status == 200:
                            return await response.json()
                        elif response.status == 429:
                            if attempt < 2:
                                await asyncio.sleep(5 * (attempt + 1))
                                continue
                            else:
                                raise Exception('レート制限により処理できませんでした')
                        else:
                            error_text = await response.text()
                            print(f'OAuth2エラー詳細: {error_text}')
                            raise Exception(f'トークン取得失敗: {response.status} - {error_text}')
            except Exception as e:
                if attempt < 2 and 'レート制限' not in str(e):
                    await asyncio.sleep(2)
                    continue
                raise

    async def get_user_info(self, access_token):
        """アクセストークンからユーザー情報を取得"""
        headers = {'Authorization': f'Bearer {access_token}'}

        # レート制限対策でリトライ
        for attempt in range(3):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(USER_URL, headers=headers) as response:
                        if response.status == 200:
                            return await response.json()
                        elif response.status == 429:
                            if attempt < 2:
                                await asyncio.sleep(5 * (attempt + 1))
                                continue
                            else:
                                raise Exception('レート制限により処理できませんでした')
                        else:
                            raise Exception(f'ユーザー情報取得失敗: {response.status}')
            except Exception as e:
                if attempt < 2 and 'レート制限' not in str(e):
                    await asyncio.sleep(2)
                    continue
                raise

    async def add_member_to_guild(self, access_token, user_id, guild_id):
        """ユーザーを指定されたサーバーに追加"""
        url = GUILD_MEMBER_URL.format(guild_id, user_id)
        headers = {
            'Authorization': f'Bot {BOT_TOKEN}',
            'Content-Type': 'application/json'
        }
        data = {'access_token': access_token}

        print(f'🌐 Discord API呼び出し: PUT {url}')

        # 複数回試行
        for attempt in range(3):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.put(url, headers=headers, json=data) as response:
                        status = response.status
                        error_text = await response.text()

                        if status == 201:
                            print(f'メンバーがサーバーに参加したよ！')
                            return True
                        elif status in [200, 204]:
                            print(f'既にサーバーのメンバーです！')
                            return True
                        elif status == 403:
                            print(f'サーバーに参加する権限がないっぽいです！')
                            print(f'📄 詳細: {error_text}')
                            return False
                        elif status == 400:
                            print(f'無効なリクエストだよ！')
                            print(f'📄 詳細: {error_text}')
                            return False
                        elif status == 429:
                            print(f'⏰ レート制限に達しました。試行 {attempt + 1}/3')
                            if attempt < 2:
                                await asyncio.sleep(5)  # 5秒待機
                                continue
                            return False
                        else:
                            print(f'❌ メンバー追加API失敗 (ステータス: {status})')
                            print(f'📄 エラー詳細: {error_text}')
                            if attempt < 2:
                                await asyncio.sleep(2)
                                continue
                            return False
            except Exception as e:
                print(f'❌ API呼び出しエラー (試行 {attempt + 1}/3): {e}')
                if attempt < 2:
                    await asyncio.sleep(2)
                    continue
                return False

        return False

    async def assign_role(self, user_id, guild_id, role_id):
        """ユーザーに指定されたサーバーでロールを付与（最初からAPI呼び出しを使用）"""
        print(f'ロール付与を API 経由で実行中: User {user_id}, Role {role_id}, Guild {guild_id}')
        return await self.assign_role_via_api(user_id, guild_id, role_id)

    async def assign_role_via_api(self, user_id, guild_id, role_id):
        """Discord APIを直接使用してロールを付与"""
        try:
            url = f"https://discord.com/api/guilds/{guild_id}/members/{user_id}/roles/{role_id}"
            headers = {
                'Authorization': f'Bot {BOT_TOKEN}',
                'Content-Type': 'application/json'
            }

            async with aiohttp.ClientSession() as session:
                async with session.put(url, headers=headers) as response:
                    if response.status == 204:
                        print(f'API経由でロール付与成功: User {user_id}, Role {role_id}, Guild {guild_id}')
                        return True
                    else:
                        error_text = await response.text()
                        print(f'API経由でのロール付与失敗 ({response.status}): {error_text}')
                        return False
        except Exception as e:
            print(f'API経由でのロール付与エラー: {e}')
            return False

    def parse_time_string(self, time_str):
        """時間文字列（d:h:m:s形式）を秒数に変換"""
        try:
            parts = time_str.split(':')
            if len(parts) != 4:
                return None

            days, hours, minutes, seconds = map(int, parts)
            total_seconds = days * 86400 + hours * 3600 + minutes * 60 + seconds

            if total_seconds <= 0:
                return None

            return total_seconds
        except ValueError:
            return None

    def format_time_remaining(self, seconds):
        """残り時間を読みやすい形式に変換"""
        days = seconds // 86400
        hours = (seconds % 86400) // 3600
        minutes = (seconds % 3600) // 60
        secs = seconds % 60

        parts = []
        if days > 0:
            parts.append(f"{days}日")
        if hours > 0:
            parts.append(f"{hours}時間")
        if minutes > 0:
            parts.append(f"{minutes}分")
        if secs > 0:
            parts.append(f"{secs}秒")

        return "".join(parts) if parts else "0秒"

    async def scheduled_nuke_task(self, channel, delay_seconds, author_name):
        """定期削除タスク"""
        try:
            await asyncio.sleep(delay_seconds)

            # チャンネルが削除されていないかチェック
            try:
                await channel.fetch_message(channel.last_message_id)
            except:
                # チャンネルが既に削除されている
                return

            # チャンネル情報を保存
            guild = channel.guild
            channel_name = channel.name
            channel_topic = getattr(channel, 'topic', None)
            channel_category = channel.category
            channel_position = channel.position
            channel_nsfw = getattr(channel, 'nsfw', False)
            channel_slowmode = getattr(channel, 'slowmode_delay', 0)

            # 権限設定を保存
            overwrites = {}
            for target, overwrite in channel.overwrites.items():
                overwrites[target] = overwrite

            # 新しいチャンネルを作成
            new_channel = await guild.create_text_channel(
                name=channel_name,
                topic=channel_topic,
                category=channel_category,
                position=channel_position,
                nsfw=channel_nsfw,
                slowmode_delay=channel_slowmode,
                overwrites=overwrites
            )

            # 元のチャンネルを削除
            await channel.delete(reason=f"定期nuke実行 - {author_name}")

            # 成功メッセージを新しいチャンネルに送信
            success_embed = discord.Embed(
                title="定期nuke完了",
                description=f"チャンネル「{channel_name}」が定期nukeによりnukeされたよ！\nじっこうしゃ: {author_name}",
                color=get_random_color(),
                timestamp=discord.utils.utcnow()
            )
            await new_channel.send(embed=success_embed)

            print(f'定期nuke実行: チャンネル「{channel_name}」がnukeされました (実行者: {author_name})')

        except asyncio.CancelledError:
            print(f'定期nuke がキャンセルされました: {channel.name}')
        except Exception as e:
            print(f'定期nuke エラー: {e}')
        finally:
            # タスクリストから削除
            if channel.id in self.scheduled_nukes:
                del self.scheduled_nukes[channel.id]

def parse_giveaway_duration(duration_str):
    """ギブアウェイの期限文字列（1w2d3h30m形式）を秒数に変換"""
    import re

    # 正規表現パターン（週、日、時間、分）
    pattern = r'(?:(\d+)w)?(?:(\d+)d)?(?:(\d+)h)?(?:(\d+)m)?'
    match = re.match(pattern, duration_str.lower())

    if not match:
        return None

    weeks, days, hours, minutes = match.groups()

    total_seconds = 0
    if weeks:
        total_seconds += int(weeks) * 604800  # 1週間 = 604800秒
    if days:
        total_seconds += int(days) * 86400    # 1日 = 86400秒
    if hours:
        total_seconds += int(hours) * 3600    # 1時間 = 3600秒
    if minutes:
        total_seconds += int(minutes) * 60    # 1分 = 60秒

    return total_seconds if total_seconds > 0 else None

def format_duration(seconds):
    """秒数を読みやすい期限形式に変換"""
    weeks = seconds // 604800
    days = (seconds % 604800) // 86400
    hours = (seconds % 86400) // 3600
    minutes = (seconds % 3600) // 60

    parts = []
    if weeks > 0:
        parts.append(f"{weeks}週間")
    if days > 0:
        parts.append(f"{days}日")
    if hours > 0:
        parts.append(f"{hours}時間")
    if minutes > 0:
        parts.append(f"{minutes}分")

    return "".join(parts) if parts else "0分"

async def end_giveaway_task(channel, giveaway_view, prize, winners, end_time, host):
    """ギブアウェイ終了タスク"""
    try:
        # 終了時刻まで待機
        now = discord.utils.utcnow()
        if end_time > now:
            wait_seconds = (end_time - now).total_seconds()
            await asyncio.sleep(wait_seconds)

        # 参加者から抽選
        participants = list(giveaway_view.participants)

        if len(participants) == 0:
            # 参加者がいない場合
            no_participants_embed = discord.Embed(
                title="giveaway終了",
                description=f"**景品:** {prize}\n\n"
                           f"❌ 参加者がいないよぉ...\n"
                           f"giveawayが無効になったよ！",
                color=get_random_color(),
                timestamp=discord.utils.utcnow()
            )
            no_participants_embed.set_footer(
                text=f"開催者: {host.display_name}",
                icon_url=host.display_avatar.url
            )

            await channel.send(embed=no_participants_embed)
            return

        # 勝者を抽選
        actual_winners = min(winners, len(participants))
        winner_ids = random.sample(participants, actual_winners)

        # 勝者の情報を取得
        winner_mentions = []
        for winner_id in winner_ids:
            try:
                member = channel.guild.get_member(winner_id)
                if member:
                    winner_mentions.append(member.mention)
                else:
                    # メンバーが見つからない場合はフェッチを試行
                    try:
                        member = await channel.guild.fetch_member(winner_id)
                        winner_mentions.append(member.mention)
                    except:
                        winner_mentions.append(f"<@{winner_id}>")
            except:
                winner_mentions.append(f"<@{winner_id}>")

        # 結果のEmbedを作成
        result_embed = discord.Embed(
            title="giveaway終了！",
            description=f"**景品:** {prize}\n\n"
                       f"🏆 **勝者（{actual_winners}人）:**\n" + "\n".join(winner_mentions) + "\n\n"
                       f"おめでとうございます！",
            color=get_random_color(),
            timestamp=discord.utils.utcnow()
        )

        result_embed.add_field(
            name="📊 参加者など",
            value=f"giveaway参加者数: {len(participants)}人\n"
        )

        result_embed.set_footer(
            text=f"主催者: {host.display_name}",
            icon_url=host.display_avatar.url
        )

        # 勝者にメンション
        winner_mentions_str = " ".join(winner_mentions)
        await channel.send(content=f"🎉 {winner_mentions_str}", embed=result_embed)

        print(f'giveaway「{prize}」が終了したよ！。勝者: {len(winner_ids)}人、参加者数: {len(participants)}人')

    except asyncio.CancelledError:
        print(f'giveaway「{prize}」がキャンセルされました！')
    except Exception as e:
        print(f'giveawayの終了エラー: {e}')

# ボットコマンド
bot = OAuthBot()

class AuthLinkView(discord.ui.View):
    def __init__(self, guild, role):
        super().__init__(timeout=None)
        self.guild = guild
        self.role = role

        # OAuth2認証リンクを生成
        params = {
            'client_id': CLIENT_ID,
            'redirect_uri': REDIRECT_URI,
            'response_type': 'code',
            'scope': 'identify guilds.join',
            'state': f'discord_oauth_{guild.id}_{role.id}'
        }
        oauth_link = f"{OAUTH_URL_BASE}?{urlencode(params)}"

        # OAuth2リンクボタンを追加
        self.add_item(discord.ui.Button(
            label='にんしょう！',
            style=discord.ButtonStyle.link,
            url=oauth_link
        ))



class RoleSelectView(discord.ui.View):
    def __init__(self, guild):
        super().__init__(timeout=300)
        self.guild = guild

        # @everyoneとBotロールを除外してセレクトメニューに追加
        options = []
        for role in guild.roles:
            if role.name != "@everyone" and not role.managed and not role.is_bot_managed():
                options.append(discord.SelectOption(
                    label=role.name,
                    value=str(role.id),
                    description=f"ID: {role.id}"
                ))

        # 最大25個までしか表示できないため、必要に応じて制限
        if len(options) > 25:
            options = options[:25]

        # オプションが存在する場合のみセレクトメニューを設定
        if options:
            self.role_select.options = options
        else:
            # ロールがない場合はセレクトメニューを削除
            self.remove_item(self.role_select)

    @discord.ui.select(
        placeholder="付与したいロールを選択してください...",
        min_values=1,
        max_values=1
    )
    async def role_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        role_id = int(select.values[0])
        role = self.guild.get_role(role_id)

        if not role:
            await interaction.response.send_message("選択されたロールが見つかりません。", ephemeral=True)
            return

        # 自動メッセージを送信
        view = AuthLinkView(self.guild, role)

        embed = discord.Embed(
            title="こんにちは！",
            description="ボタンを押してにんしょうしてね！",
            color=get_random_color()
        )

        await interaction.response.send_message(embed=embed, view=view)

# スラッシュコマンド
@bot.tree.command(name='role', description='認証メッセージを送信します')
@app_commands.describe(role='付与したいロールを選択してね', channel='このパネルを送るチャンネルを選択（省略した場合は現在のチャンネル）')
@app_commands.default_permissions(administrator=True)
async def role_slash(interaction: discord.Interaction, role: discord.Role, channel: discord.TextChannel = None):
    """認証メッセージを指定したチャンネルに送信"""
    target_channel = channel or interaction.channel

    view = AuthLinkView(interaction.guild, role)

    embed = discord.Embed(
        title="こんにちは！",
        description="ボタンを押してにんしょうしてね！",
        color=get_random_color()
    )

    await target_channel.send(embed=embed, view=view)
    await interaction.response.send_message(f"認証メッセージを {target_channel.mention} に送信しました", ephemeral=True)

@bot.tree.command(name='call', description='麺爆機能です、使えないです')
@app_commands.default_permissions(administrator=True)
async def call_slash(interaction: discord.Interaction):
    """ただの麺爆機能"""

    # mume_dayoユーザーのみ使用可能
    if interaction.user.name != 'mume_dayo':
        await interaction.response.send_message("❌ このコマンドはむめーのみしか使えません、ごめんね", ephemeral=True)
        return

    # レスポンスを遅延（処理時間がかかる可能性があるため）
    await interaction.response.defer()

    current_guild = interaction.guild
    current_guild_id = current_guild.id

    # 全サーバーから認証済みユーザーを収集（現在のサーバー以外）
    users_to_add = []
    already_members = []

    for guild_id, user_ids in bot.authenticated_users.items():
        # 現在のサーバーはスキップ
        if guild_id == current_guild_id:
            continue

        for user_id in user_ids:
            try:
                # 既にそのサーバーのメンバーかチェック
                existing_member = current_guild.get_member(int(user_id))
                if existing_member:
                    already_members.append(existing_member)
                    print(f'ユーザー {existing_member.name} は既に {current_guild.name} のメンバーです')
                    continue

                # アクセストークンが保存されているかチェック
                if user_id in bot.user_tokens:
                    user = await bot.fetch_user(int(user_id))
                    users_to_add.append({
                        'user_id': user_id,
                        'user': user,
                        'access_token': bot.user_tokens[user_id],
                        'source_guild_id': guild_id
                    })
                else:
                    print(f'ユーザー {user_id} のアクセストークンが見つからないお！')

            except Exception as e:
                print(f'ユーザー {user_id} の情報取得エラー: {e}')
                continue

    # 結果統計を準備
    already_member_count = len(already_members)
    added_count = 0
    failed_count = 0

    # 参加させる必要があるユーザーがいる場合
    if users_to_add:
        for user_data in users_to_add:
            user_id = user_data['user_id']
            user = user_data['user']
            access_token = user_data['access_token']
            source_guild_id = user_data['source_guild_id']

            try:
                # 保存されたアクセストークンを使って直接サーバーに参加
                print(f'ユーザー {user.name} を {current_guild.name} に参加させています...')
                success = await bot.add_member_to_guild(access_token, user_id, current_guild_id)

                if success:
                    # サーバー参加の確認
                    member_found = False
                    for attempt in range(5):  # 最大5回試行
                        try:
                            member = await current_guild.fetch_member(int(user_id))
                            print(f'✅ メンバー参加確認: {member.display_name} ({member.name})')
                            added_count += 1
                            member_found = True

                            # 認証済みユーザーリストに追加
                            if current_guild_id not in bot.authenticated_users:
                                bot.authenticated_users[current_guild_id] = []
                            if user_id not in bot.authenticated_users[current_guild_id]:
                                bot.authenticated_users[current_guild_id].append(user_id)

                            break
                        except discord.NotFound:
                            print(f'⚠️ 試行 {attempt + 1}/5: メンバー {user_id} がまだ参加していません')
                            if attempt < 4:
                                await asyncio.sleep(2)
                        except Exception as e:
                            print(f'❌ メンバー確認エラー: {e}')
                            if attempt < 4:
                                await asyncio.sleep(2)

                    if not member_found:
                        failed_count += 1
                        print(f'❌ {user.name} の参加を確認できませんでした')
                else:
                    failed_count += 1
                    print(f'❌ {user.name} の参加に失敗しました')

            except Exception as e:
                failed_count += 1
                print(f'❌ {user.name} の追加エラー: {e}')

    # 結果メッセージを作成
    total_processed = already_member_count + added_count + failed_count

    if total_processed == 0:
        await interaction.followup.send("追加対象の認証済みユーザーが見つかりませんでした。\n\n"
                                       "• 他のサーバーで認証済みのユーザーがいません\n"
                                       "• アクセストークンが保存されているユーザーがいません", ephemeral=True)
        return

    result_message = f"**サーバー参加結果** - {current_guild.name}\n\n"

    if already_member_count > 0:
        result_message += f" 既にメンバー: {already_member_count}人\n"

    if added_count > 0:
        result_message += f" 新規参加: {added_count}人\n"

    if failed_count > 0:
        result_message += f" 参加失敗: {failed_count}人\n"

    result_message += f"\n**合計処理数:** {total_processed}人"

    await interaction.followup.send(result_message, ephemeral=True)

    print(f'{interaction.user.name} が認証済みユーザーの {current_guild.name} への参加を実行しました')

@bot.tree.command(name='nuke', description='チャンネルを権限を引き継いで再生成します')
@app_commands.default_permissions(administrator=True)
async def nuke_slash(interaction: discord.Interaction):
    """チャンネルを権限を引き継いで再生成する"""
    channel = interaction.channel

    # 確認メッセージを送信
    confirm_embed = discord.Embed(
        title="⚠️ チャンネル再生成の確認",
        description=f"チャンネル「{channel.name}」をnukeしますか？\n\n"
                   "nukeするんだよね！：\n"
                   "**この操作は取り消せません！**",
        color=get_random_color()
    )

    # 確認ボタンを作成
    view = NukeConfirmView(interaction.user.id)
    await interaction.response.send_message(embed=confirm_embed, view=view, ephemeral=True)
    view.message = await interaction.original_response()

@bot.tree.command(name='level', description='あなたのレベルを確認できます！')
@app_commands.describe(user='レベル情報を見たいユーザー（省略した場合は自分）')
async def level_slash(interaction: discord.Interaction, user: discord.Member = None):
    """ユーザーのレベル情報を表示"""
    target_user = user or interaction.user
    guild_id = interaction.guild.id
    user_id = str(target_user.id)

    # ユーザーのレベルデータを取得
    if guild_id not in bot.user_levels or user_id not in bot.user_levels[guild_id]:
        embed = discord.Embed(
            title="📊 レベル情報",
            description=f"{target_user.display_name} さんはまだメッセージを送信してないよ！",
            color=get_random_color()
        )
        await interaction.response.send_message(embed=embed)
        return

    user_data = bot.user_levels[guild_id][user_id]
    current_level = user_data["level"]
    current_xp = user_data["xp"]
    message_count = user_data["message_count"]

    # 次のレベルまでのXPを計算
    next_level_xp = bot.calculate_xp_for_level(current_level + 1)
    current_level_xp = bot.calculate_xp_for_level(current_level)
    xp_needed = next_level_xp - current_xp
    xp_progress = current_xp - current_level_xp
    xp_required_for_next = next_level_xp - current_level_xp

    # プログレスバーを作成
    progress_percentage = xp_progress / xp_required_for_next
    progress_bar_length = 20
    filled_bars = int(progress_percentage * progress_bar_length)
    empty_bars = progress_bar_length - filled_bars
    progress_bar = "█" * filled_bars + "░" * empty_bars

    embed = discord.Embed(
        title="📊 レベル情報",
        color=get_random_color()
    )

    embed.set_author(
        name=target_user.display_name,
        icon_url=target_user.display_avatar.url
    )

    embed.add_field(
        name="いまのれべる！",
        value=f"レベル {current_level}",
        inline=True
    )

    embed.add_field(
        name="合計ぽいんと！",
        value=f"{current_xp:,} XP",
        inline=True
    )

    embed.add_field(
        name="💬 メッセージ数",
        value=f"{message_count:,} 回",
        inline=True
    )

    embed.add_field(
        name="📈 次のレベルまで",
        value=f"{progress_bar}\n{xp_progress}/{xp_required_for_next} XP ({xp_needed} XPぐらい必要だよ)",
        inline=False
    )

    await interaction.response.send_message(embed=embed)

@bot.tree.command(name='ranking', description='れべるらんきんぐだよ！')
@app_commands.describe(page='表示するページ（1ページに10人まで）')
async def ranking_slash(interaction: discord.Interaction, page: int = 1):
    """サーバーのレベルランキングを表示"""
    guild_id = interaction.guild.id

    if guild_id not in bot.user_levels or not bot.user_levels[guild_id]:
        embed = discord.Embed(
            title="ランキングだよ！",
            description="このサーバーにはまだランキングデータないよ！もっと発言してね！",
            color=get_random_color()
        )
        await interaction.response.send_message(embed=embed)
        return

    # ユーザーデータをレベルとXPでソート
    user_data = bot.user_levels[guild_id]
    sorted_users = sorted(
        user_data.items(),
        key=lambda x: (x[1]["level"], x[1]["xp"]),
        reverse=True
    )

    # ページネーション設定
    users_per_page = 10
    total_pages = (len(sorted_users) + users_per_page - 1) // users_per_page

    if page < 1 or page > total_pages:
        embed = discord.Embed(
            title="❌ エラー",
            description=f"無効なページ番号です。1～{total_pages}の範囲で指定してね！",
            color=get_random_color()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    # 表示するユーザーを取得
    start_index = (page - 1) * users_per_page
    end_index = start_index + users_per_page
    page_users = sorted_users[start_index:end_index]

    embed = discord.Embed(
        title="ランキングだよ！",
        description=f"ページ {page}/{total_pages}",
        color=get_random_color()
    )

    ranking_text = ""
    for i, (user_id, data) in enumerate(page_users, start=start_index + 1):
        try:
            member = interaction.guild.get_member(int(user_id))
            if member:
                # ランキング位置に応じた絵文字
                if i == 1:
                    rank_emoji = "🥇"
                elif i == 2:
                    rank_emoji = "🥈"
                elif i == 3:
                    rank_emoji = "🥉"
                else:
                    rank_emoji = f"{i}."

                ranking_text += f"{rank_emoji} **{member.display_name}**\n"
                ranking_text += f"   レベル {data['level']} • {data['xp']:,} XP • {data['message_count']:,} メッセージ\n\n"
        except:
            continue

    if ranking_text:
        embed.description = f"ページ {page}/{total_pages}\n\n{ranking_text}"
    else:
        embed.description = "このページには表示するユーザーがいないよ！"

    embed.set_footer(text=f"合計 {len(sorted_users)} 人のユーザー")

    await interaction.response.send_message(embed=embed)

@bot.tree.command(name='masquerade', description='指定チャンネルにメッセージをおくるよ！')
@app_commands.describe(
    channel='メッセージを送信するチャンネル',
    message='送信するメッセージ'
)
@app_commands.default_permissions(administrator=True)
async def log_slash(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
    message: str
):
    """指定したチャンネルにユーザーになりきってメッセージを送信"""

    try:
        current_user = interaction.user

        # 指定したチャンネルにメッセージを送信（ユーザーになりきって）
        await channel.send(message)

        # 実行者に確認メッセージを送信
        await interaction.response.send_message(
            f"✅ {channel.mention} にメッセージを送信しました！\n"
            f"送信内容: {message[:100]}{'...' if len(message) > 100 else ''}",
            ephemeral=True
        )

        print(f"📤 {current_user.name} が {channel.name} にメッセージを送ったよ！: {message[:50]}...")

    except discord.Forbidden:
        await interaction.response.send_message(
            f"❌ {channel.mention} への送信権限がありません。",
            ephemeral=True
        )
    except Exception as e:
        await interaction.response.send_message(
            f"❌ メッセージ送信中にエラーが発生しました: {str(e)}",
            ephemeral=True
        )
        print(f"❌ Log command error: {e}")

@bot.tree.command(name='timenuke', description='指定した時間でnukeします')
@app_commands.describe(time='削除までの時間（d:h:m:s形式、例: 0:1:30:0 = 1時間30分後）')
@app_commands.default_permissions(administrator=True)
async def timenuke_slash(interaction: discord.Interaction, time: str):
    """指定した時間でチャンネルをnukeします"""
    channel = interaction.channel

    # 既に定期削除が設定されているかチェック
    if channel.id in bot.scheduled_nukes:
        await interaction.response.send_message(
            "❌ このチャンネルには既に定期削除が設定されています。\n"
            "`/timecancel` でnukeをキャンセルしてから再設定してね！。",
            ephemeral=True
        )
        return

    # 時間文字列を解析
    delay_seconds = bot.parse_time_string(time)
    if delay_seconds is None:
        await interaction.response.send_message(
            "❌ 時間の形式が無効です。\n"
            "正しい形式: `d:h:m:s` (例: `0:1:30:0` = 1時間30分後)",
            ephemeral=True
        )
        return

    # 最小1分、最大7日間の制限
    if delay_seconds < 60:
        await interaction.response.send_message(
            "❌ 最小時間は1分だよ！。",
            ephemeral=True
        )
        return

    if delay_seconds > 604800:  # 7日間
        await interaction.response.send_message(
            "❌ 最大時間は一週間だよ！。",
            ephemeral=True
        )
        return

    # 定期削除タスクを開始
    task = asyncio.create_task(
        bot.scheduled_nuke_task(channel, delay_seconds, interaction.user.name)
    )
    bot.scheduled_nukes[channel.id] = task

    # 確認メッセージ
    execution_time = discord.utils.utcnow() + timedelta(seconds=delay_seconds)
    time_remaining = bot.format_time_remaining(delay_seconds)

    confirm_embed = discord.Embed(
        title="定期nukeを設定しました！",
        description=f"チャンネル「{channel.name}」を**{time_remaining}後**にnukeします！",
        color=get_random_color(),
        timestamp=discord.utils.utcnow()
    )

    confirm_embed.add_field(
        name="実行時刻",
        value=discord.utils.format_dt(execution_time, style='F'),
        inline=True
    )

    confirm_embed.add_field(
        name="実行しようとしてる人",
        value=interaction.user.mention,
        inline=True
    )

    confirm_embed.add_field(
        name="注意！",
        value="`/timecancel` でキャンセル可能です",
        inline=False
    )

    await interaction.response.send_message(embed=confirm_embed)

    print(f'{interaction.user.name} がチャンネル「{channel.name}」に{time_remaining}後の定期nukeを設定しました！')

@bot.tree.command(name='timecancel', description='設定されている定期nukeをキャンセルします')
@app_commands.default_permissions(administrator=True)
async def timecancel_slash(interaction: discord.Interaction):
    """定期削除をキャンセルする"""
    channel = interaction.channel

    if channel.id not in bot.scheduled_nukes:
        await interaction.response.send_message(
            "※ このチャンネルには定期削除が設定されていません。",
            ephemeral=True
        )
        return

    # タスクをキャンセル
    task = bot.scheduled_nukes[channel.id]
    task.cancel()
    del bot.scheduled_nukes[channel.id]

    cancel_embed = discord.Embed(
        title="※ 定期nukeをキャンセルしたよ！",
        description=f"チャンネル「{channel.name}」の定期nukeがキャンセルされたよ！。",
        color=get_random_color(),
        timestamp=discord.utils.utcnow()
    )

    cancel_embed.add_field(
        name="実行者",
        value=interaction.user.mention,
        inline=True
    )

    await interaction.response.send_message(embed=cancel_embed)

    print(f'{interaction.user.name} がチャンネル「{channel.name}」の定期nukeをキャンセルしたよ！')

@bot.tree.command(name='delete', description='指定メッセージ数を削除するよ！')
@app_commands.describe(
    amount='削除するメッセージ数（1-100）',
    member='特定のメンバーのメッセージのみ削除'
)
@app_commands.default_permissions(manage_messages=True)
async def delete_slash(interaction: discord.Interaction, amount: int, member: discord.Member = None):
    """指定した数のメッセージを削除する"""

    # 削除数の制限
    if amount < 1 or amount > 100:
        await interaction.response.send_message(
            "❌ 削除数は1から100までの範囲で指定してね！",
            ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)

    try:
        channel = interaction.channel
        deleted_count = 0

        if member:
            # 特定のメンバーのメッセージを削除
            def is_target_member(message):
                return message.author == member

            # 最近のメッセージを検索して対象メンバーのものを削除
            messages_to_delete = []
            async for message in channel.history(limit=500):  # 最近500件から検索
                if is_target_member(message):
                    messages_to_delete.append(message)
                    if len(messages_to_delete) >= amount:
                        break

            # メッセージを削除
            for message in messages_to_delete:
                try:
                    await message.delete()
                    deleted_count += 1
                    await asyncio.sleep(0.5)  # レート制限対策
                except discord.NotFound:
                    continue
                except discord.Forbidden:
                    break
                except Exception as e:
                    print(f"メッセージ削除エラー: {e}")
                    continue

            result_embed = discord.Embed(
                title="メッセージ削除完了！",
                description=f"{member.mention} のメッセージを **{deleted_count}件** 削除したよ！。",
                color=get_random_color(),
                timestamp=discord.utils.utcnow()
            )

        else:
            # 最新のメッセージを削除
            deleted_messages = []
            async for message in channel.history(limit=amount):
                deleted_messages.append(message)

            # 14日以内のメッセージのみ一括削除可能
            recent_messages = []
            old_messages = []
            cutoff_date = discord.utils.utcnow() - timedelta(days=14)

            for message in deleted_messages:
                if message.created_at > cutoff_date:
                    recent_messages.append(message)
                else:
                    old_messages.append(message)

            # 最近のメッセージを一括削除
            if recent_messages:
                try:
                    await channel.delete_messages(recent_messages)
                    deleted_count += len(recent_messages)
                except discord.Forbidden:
                    # 権限がない場合は個別削除
                    for message in recent_messages:
                        try:
                            await message.delete()
                            deleted_count += 1
                            await asyncio.sleep(0.5)
                        except:
                            continue

            # 古いメッセージを個別削除
            for message in old_messages:
                try:
                    await message.delete()
                    deleted_count += 1
                    await asyncio.sleep(0.5)
                except:
                    continue

            result_embed = discord.Embed(
                title="メッセージ削除完了",
                description=f"最新のメッセージから **{deleted_count}件** 削除したよ！",
                color=get_random_color(),
                timestamp=discord.utils.utcnow()
            )

        result_embed.add_field(
            name="実行者",
            value=interaction.user.mention,
            inline=True
        )

        result_embed.add_field(
            name="チャンネル",
            value=f"#{channel.name}",
            inline=True
        )

        await interaction.followup.send(embed=result_embed, ephemeral=True)

        print(f'{interaction.user.name} が {channel.name} で {deleted_count}件のメッセージを削除したよ！ (対象: {member.name if member else "全員"})')

    except Exception as e:
        await interaction.followup.send(
            f"※ メッセージ削除中にエラーが発生しました: {str(e)}",
            ephemeral=True
        )
        print(f"Delete command error: {e}")

@bot.tree.command(name='vending_setup', description='ペイリンクと許可ボタンの送信場所を選択できます！')
@app_commands.default_permissions(administrator=True)
async def vending_setup_slash(interaction: discord.Interaction):
    """販売機の管理者チャンネルを設定"""
    guild_id = interaction.guild.id
    channel_id = interaction.channel.id
    vending_machine = bot.get_guild_vending_machine(guild_id)

    if channel_id in vending_machine['admin_channels']:
        await interaction.response.send_message(
            "このチャンネルですでに完了してるよ！。",
            ephemeral=True
        )
        return

    vending_machine['admin_channels'].add(channel_id)

    setup_embed = discord.Embed(
        title="管理者チャンネル設定完了！",
        description=f"このチャンネルが販売機の管理者チャンネルとして設定されました。\n"
                   f"認証リンクなどはここに送信されます。",
        color=get_random_color(),
        timestamp=discord.utils.utcnow()
    )

    await interaction.response.send_message(embed=setup_embed)
    print(f'{interaction.user.name} がチャンネル {interaction.channel.name} を販売機管理者チャンネルに設定したよ！')

@bot.tree.command(name='add_product', description='販売機に商品を追加(商品名)できます')
@app_commands.describe(
    product_id='商品ID（英数字）',
    name='商品名',
    price='価格',
    description='商品説明',
    stock='在庫数'
)
@app_commands.default_permissions(administrator=True)
async def add_product_slash(
    interaction: discord.Interaction,
    product_id: str,
    name: str,
    price: int,
    description: str,
    stock: int = 1
):
    """販売機に商品を追加"""
    if not product_id.replace('_', '').isalnum():
        await interaction.response.send_message(
            "商品IDは英数字のみ使用可能です！",
            ephemeral=True
        )
        return

    if price < 1:
        await interaction.response.send_message(
            "価格は1円以上で設定してね！",
            ephemeral=True
        )
        return

    if stock < 0:
        await interaction.response.send_message(
            "在庫数は0以上で設定してね！",
            ephemeral=True
        )
        return

    guild_id = interaction.guild.id
    vending_machine = bot.get_guild_vending_machine(guild_id)

    vending_machine['products'][product_id] = {
        'name': name,
        'price': price,
        'description': description,
        'stock': stock,
        'inventory': []  # 事前に追加された在庫アイテムのリスト
    }

    product_embed = discord.Embed(
        title="✅ 商品追加完了",
        description=f"商品「{name}」が販売機に追加されました。",
        color=get_random_color()
    )

    product_embed.add_field(name="商品ID", value=product_id, inline=True)
    product_embed.add_field(name="価格", value=f"¥{price:,}", inline=True)
    product_embed.add_field(name="在庫", value=f"{stock}個", inline=True)
    product_embed.add_field(name="説明", value=description, inline=False)
    product_embed.add_field(
        name="次に...", 
        value=f"`/add_inventory {product_id}` で在庫アイテムを追加してください", 
        inline=False
    )

    await interaction.response.send_message(embed=product_embed)
    print(f'{interaction.user.name} が商品「{name}」を販売機に追加しました')

@bot.tree.command(name='add_inventory', description='在庫を追加します(一個ずつ)')
@app_commands.describe(
    product_id='商品ID',
    item_content='在庫アイテムの内容（購入時にDMで送信される内容）'
)
@app_commands.default_permissions(administrator=True)
async def add_inventory_slash(
    interaction: discord.Interaction,
    product_id: str,
    item_content: str
):
    """商品に在庫アイテムを追加"""
    guild_id = interaction.guild.id
    vending_machine = bot.get_guild_vending_machine(guild_id)

    if product_id not in vending_machine['products']:
        await interaction.response.send_message(
            f"❌ 商品ID「{product_id}」が見つからないよ！。",
            ephemeral=True
        )
        return

    product = vending_machine['products'][product_id]

    # 在庫アイテムを追加
    if 'inventory' not in product:
        product['inventory'] = []

    product['inventory'].append(item_content)
    product['stock'] = len(product['inventory'])  # 在庫数を実際のアイテム数に更新

    inventory_embed = discord.Embed(
        title="在庫追加完了！",
        description=f"商品「{product['name']}」に在庫アイテムを追加しました。",
        color=get_random_color()
    )

    inventory_embed.add_field(name="商品ID", value=product_id, inline=True)
    inventory_embed.add_field(name="現在の在庫数", value=f"{product['stock']}個", inline=True)
    inventory_embed.add_field(name="追加された内容", value=item_content[:100] + ("..." if len(item_content) > 100 else ""), inline=False)

    await interaction.response.send_message(embed=inventory_embed)
    print(f'{interaction.user.name} が商品「{product["name"]}」に在庫アイテムを追加しました！')

@bot.tree.command(name='view_inventory', description='商品の在庫一覧を表示します')
@app_commands.describe(product_id='商品ID')
@app_commands.default_permissions(administrator=True)
async def view_inventory_slash(interaction: discord.Interaction, product_id: str):
    """商品の在庫アイテム一覧を表示"""
    guild_id = interaction.guild.id
    vending_machine = bot.get_guild_vending_machine(guild_id)

    if product_id not in vending_machine['products']:
        await interaction.response.send_message(
            f"❌ 商品ID「{product_id}」が見つからないよ！",
            ephemeral=True
        )
        return

    product = vending_machine['products'][product_id]
    inventory = product.get('inventory', [])

    if not inventory:
        await interaction.response.send_message(
            f"商品「{product['name']}」には在庫がないよ！",
            ephemeral=True
        )
        return

    inventory_embed = discord.Embed(
        title=f"在庫一覧 - {product['name']}",
        description=f"商品ID: {product_id}\n在庫数: {len(inventory)}個",
        color=get_random_color()
    )

    for i, item in enumerate(inventory, 1):
        inventory_embed.add_field(
            name=f"在庫アイテム #{i}",
            value=item[:100] + ("..." if len(item) > 100 else ""),
            inline=False
        )

    await interaction.response.send_message(embed=inventory_embed, ephemeral=True)

@bot.tree.command(name='vending_panel', description='販売機パネルを設置するよ！')
@app_commands.describe(
    admin_channel='管理者チャンネル）',
    achievement_channel='実績チャンネル（購入実績を自動送信するよ！）'
)
@app_commands.default_permissions(administrator=True)
async def vending_panel_slash(
    interaction: discord.Interaction, 
    admin_channel: discord.TextChannel = None,
    achievement_channel: discord.TextChannel = None
):
    """販売機パネルを設置"""
    guild_id = interaction.guild.id
    vending_machine = bot.get_guild_vending_machine(guild_id)

    if not vending_machine['products']:
        await interaction.response.send_message(
            "販売する商品がないよ！。先に `/add_product` で商品を追加してね！",
            ephemeral=True
        )
        return

    # 管理者チャンネルが指定された場合は追加
    if admin_channel:
        vending_machine['admin_channels'].add(admin_channel.id)
        print(f'{interaction.user.name} がチャンネル {admin_channel.name} を販売機管理者チャンネルに設定しました')

    # 実績チャンネルが指定された場合は設定
    if achievement_channel:
        vending_machine['achievement_channel'] = achievement_channel.id
        print(f'{interaction.user.name} がチャンネル {achievement_channel.name} を実績チャンネルに設定しました')

    if not vending_machine['admin_channels']:
        await interaction.response.send_message(
            "管理者チャンネルが設定されないよ！、先に `/vending_setup` で設定してね！",
            ephemeral=True
        )
        return

    panel_embed = discord.Embed(
        title="半販売機",
        description="購入したい商品を選択してね！。\n"
                   "購入後、リンクが確認できたらDMで商品をおくります！。",
        color=get_random_color()
    )
    product_list = ""
    for product_id, product in vending_machine['products'].items():
        actual_stock = len(product.get('inventory', []))
        stock_status = f"在庫: {actual_stock}個" if actual_stock > 0 else "在庫切れ"
        product_list += f"**{product['name']}** - ¥{product['price']:,}\n{product['description']}\n{stock_status}\n\n"

    panel_embed.add_field(
        name="商品一覧",
        value=product_list,
        inline=False
    )

    panel_embed.set_footer(text="made by mumei")

    # 実績チャンネルが設定されている場合の表示
    if achievement_channel:
        panel_embed.add_field(
            name="実績チャンネル", 
            value=f"購入実績が {achievement_channel.mention} に自動送信されるよ！",
            inline=False
        )

    view = VendingMachineView(guild_id)
    await interaction.response.send_message(embed=panel_embed, view=view)
    print(f'{interaction.user.name} が販売機パネルを設置しました')

@bot.tree.command(name='giveaway', description='giveawayを作成します！')
@app_commands.describe(
    prize='景品',
    winners='人数（1-10）',
    duration='期限（例: 1w2d3h30m = 1週間2日3時間30分）'
)
@app_commands.default_permissions(administrator=True)
async def giveaway_slash(interaction: discord.Interaction, prize: str, winners: int, duration: str):
    """giveawayを作成"""

    # 勝者数の範囲チェック
    if winners < 1 or winners > 10:
        await interaction.response.send_message(
            "人数は1から10までの範囲で指定してね！",
            ephemeral=True
        )
        return

    # 期限の解析
    duration_seconds = parse_giveaway_duration(duration)
    if duration_seconds is None:
        await interaction.response.send_message(
            "期限の形式が無効だよ！\n"
            "正しい形式は: `1w2d3h30m` (1週間2日3時間30分)\n"
            "使用可能単位: w(週), d(日), h(時間), m(分)",
            ephemeral=True
        )
        return

    # 最小1分、最大4週間の制限
    if duration_seconds < 60:
        await interaction.response.send_message(
            "最小期限は1分からです！。",
            ephemeral=True
        )
        return

    if duration_seconds > 2419200:  # 4週間
        await interaction.response.send_message(
            "最大期限は4週間です！",
            ephemeral=True
        )
        return

    # ギブアウェイの終了時刻を計算
    end_time = discord.utils.utcnow() + timedelta(seconds=duration_seconds)

    # ギブアウェイEmbedを作成
    giveaway_embed = discord.Embed(
        title="ギブアウェイ開催中！",
        description=f"**景品:** {prize}\n"
                   f"**勝者数:** {winners}人\n"
                   f"**終了時刻:** {discord.utils.format_dt(end_time, style='F')}\n"
                   f"**残り時間:** {discord.utils.format_dt(end_time, style='R')}\n\n"
                   f"🎁 参加するには下の「参加」ボタンをクリック！",
        color=get_random_color(),
        timestamp=discord.utils.utcnow()
    )

    giveaway_embed.set_footer(
        text=f"主催者: {interaction.user.display_name}",
        icon_url=interaction.user.display_avatar.url
    )

    # ギブアウェイビューを作成
    giveaway_view = GiveawayView(prize, winners, end_time, interaction.user.id)

    await interaction.response.send_message(embed=giveaway_embed, view=giveaway_view)

    # ギブアウェイ終了タスクをスケジュール
    asyncio.create_task(end_giveaway_task(
        interaction.channel,
        giveaway_view,
        prize,
        winners,
        end_time,
        interaction.user
    ))

    print(f'{interaction.user.name} がgiveaway「{prize}」を開始しました（勝者{winners}人、期限{format_duration(duration_seconds)}）')

@bot.tree.command(name='help', description='m.m.VDの機能一覧を表示します')
async def help_slash(interaction: discord.Interaction):
    """ボットの機能一覧を表示"""

    # メインのヘルプEmbed
    help_embed = discord.Embed(
        title="m.m.VD機能一覧",
        description="このbotの使える機能の一覧です！。",
        color=get_random_color(),
        timestamp=discord.utils.utcnow()
    )

    # 認証システム機能
    auth_commands = [
        "`/role` - 認証メッセージを送信します！",
        "`/call` - 他サーバーの認証済みユーザーを招待(現在使用不可)"
    ]
    help_embed.add_field(
        name="認証系だよ！",
        value="\n".join(auth_commands),
        inline=False
    )

    # レベルシステム機能
    level_commands = [
        "`/level [ユーザー]` - レベル情報を表示するよ！",
        "`/ranking [ページ]` - サーバーランキングを表示するよ！"
    ]
    help_embed.add_field(
        name="レベル系統",
        value="\n".join(level_commands),
        inline=False
    )



    # 販売機システム機能
    vending_commands = [
        "`/vending_setup` - 管理者チャンネル設定できます",
        "`/add_product` - 商品追加ができます",
        "`/add_inventory` - 商品在庫確認ができます",
        "`/view_inventory` - 在庫確認ができます",
        "`/vending_panel` - 自販機を設置します"
    ]
    help_embed.add_field(
        name="半自動販売機系統",
        value="\n".join(vending_commands),
        inline=False
    )

    # チケットシステム機能
    ticket_commands = [
        "`/ticket_panel` - チケット作成パネル設置"
    ]
    help_embed.add_field(
        name="🎫 チケットシステム",
        value="\n".join(ticket_commands),
        inline=False
    )
    # チャンネル管理機能
    channel_commands = [
        "`/nuke` - チャンネルをnukeします",
        "`/timenuke <時間>` - 時間指定でnukeします",
        "`/timecancel` - 定期nukeをキャンセルできます",
        "`/delete <数> [ユーザー]` - 指定数のメッセージを削除できます"
    ]
    help_embed.add_field(
        name="色々",
        value="\n".join(channel_commands),
        inline=False
    )

    # ギブアウェイ機能
    giveaway_commands = [
        "`/giveaway <景品> <人数> <期限>` - giveawayを開けます"
    ]
    help_embed.add_field(
        name="giveaway",
        value="\n".join(giveaway_commands),
        inline=False
    )

    # その他の機能
    other_commands = [
        "`/masquerade <チャンネル> <メッセージ>` - メッセージ送信ができます",
        "`/help` - この機能一覧を表示します"
    ]
    help_embed.add_field(
        name="その他",
        value="\n".join(other_commands),
        inline=False
    )

    # ボット情報
    help_embed.add_field(
        name="ボット情報",
        value=f"参加サーバー数: {len(bot.guilds)}個\n"
              f"利用制限: なし（無期限利用可能）",
        inline=False
    )

    help_embed.set_footer(
        text="管理者限定コマンドは権限が必要だよ！",
        icon_url=bot.user.display_avatar.url if bot.user else None
    )

    await interaction.response.send_message(embed=help_embed)
    print(f'{interaction.user.name} が /help コマンドを使用しました')

@bot.tree.command(name='ticket_panel', description='チケット作成パネルを設置します！')
@app_commands.describe(
    title='パネルのタイトル',
    description='パネルの説明文',
    category='チケットを作成するカテゴリ'
)
@app_commands.default_permissions(administrator=True)
async def ticket_panel_slash(
    interaction: discord.Interaction,
    title: str = "チケット",
    description: str = "チケットを開きたい方は、下記のボタンから開いてください",
    category: discord.CategoryChannel = None
):
    """チケット作成パネルを設置"""

    # パネル用のEmbed作成
    panel_embed = discord.Embed(
        title=f"🎫 {title}",
        description=f"🎫\n\n{description}",
        color=get_random_color()
    )

    panel_embed.add_field(
        name="使い方",
        value="ボタンを押してね",
        inline=False
    )

    # チケット作成ボタン付きのViewを作成
    view = TicketPanelView(category)

    await interaction.response.send_message(embed=panel_embed, view=view)

    print(f'{interaction.user.name} がチケットパネルを設置しました')

class VendingMachineView(discord.ui.View):
    def __init__(self, guild_id):
        super().__init__(timeout=None)
        self.guild_id = guild_id

        # 商品選択用のセレクトメニューを作成
        vending_machine = bot.get_guild_vending_machine(guild_id)
        options = []
        for product_id, product in vending_machine['products'].items():
            actual_stock = len(product.get('inventory', []))
            if actual_stock > 0:
                options.append(discord.SelectOption(
                    label=f"{product['name']} - ¥{product['price']:,}",
                    value=product_id,
                    description=product['description'][:100]
                ))

        if options:
            self.product_select.options = options
        else:
            self.remove_item(self.product_select)

    @discord.ui.select(
        placeholder="購入する商品を選択してください...",
        min_values=1,
        max_values=1
    )
    async def product_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        product_id = select.values[0]
        vending_machine = bot.get_guild_vending_machine(self.guild_id)
        product = vending_machine['products'].get(product_id)

        if not product:
            await interaction.response.send_message(
                "選択された商品が見つからないよ！。",
                ephemeral=True
            )
            return

        # 実際の在庫アイテム数をチェック
        inventory = product.get('inventory', [])
        if len(inventory) <= 0:
            await interaction.response.send_message(
                "この商品は在庫切れだよ！。",
                ephemeral=True
            )
            return

        # 注文IDを生成
        order_id = vending_machine['next_order_id']
        vending_machine['next_order_id'] += 1

        # 注文を記録（在庫は確認時まで減らさない）
        vending_machine['orders'][str(order_id)] = {
            'user_id': str(interaction.user.id),
            'product_id': product_id,
            'status': 'pending_payment',
            'channel_id': interaction.channel.id,
            'timestamp': time.time(),
            'processed_by': None,  # 処理者のユーザーID
            'processed_at': None   # 処理日時
        }



        # ユーザーに確認メッセージを送信
        purchase_embed = discord.Embed(
            title="🛒 商品注文完了",
            description=f"**{product['name']}** の注文を受け付けました。\n"
                       f"管理者が支払いを確認次第、DMで商品を送るよ！。",
            color=get_random_color()
        )

        purchase_embed.add_field(name="注文ID", value=f"#{order_id}", inline=True)
        purchase_embed.add_field(name="金額", value=f"¥{product['price']:,}", inline=True)
        purchase_embed.add_field(name="ステータス", value="支払い確認待ち", inline=True)

        # PayPayリンク入力モーダルを表示
        await interaction.response.send_modal(PayPayLinkModal(order_id, product, self.guild_id))
        print(f'{interaction.user.name} が商品「{product["name"]}」を注文しました！ (注文ID: {order_id})')

    async def send_admin_notification(self, channel, order_id, user, product, paypay_link):
        """管理者チャンネルに通知を送信"""
        admin_embed = discord.Embed(
            title="💰 新規注文通知",
            description=f"新しい注文が入ったよ！。",
            color=get_random_color(),
            timestamp=discord.utils.utcnow()
        )

        admin_embed.add_field(name="注文ID", value=f"#{order_id}", inline=True)
        admin_embed.add_field(name="購入者", value=f"{user.mention}\n({user.name})", inline=True)
        admin_embed.add_field(name="買いたい商品", value=product['name'], inline=True)
        admin_embed.add_field(name="金額", value=f"¥{product['price']:,}", inline=True)
        admin_embed.add_field(name="PayPayリンク", value=f"[支払いリンク]({paypay_link})", inline=False)

        admin_embed.set_thumbnail(url=user.display_avatar.url)

        view = AdminApprovalView(order_id)
        await channel.send(embed=admin_embed, view=view)

class PayPayLinkModal(discord.ui.Modal, title='PayPay支払いリンク入力'):
    def __init__(self, order_id, product, guild_id):
        super().__init__()
        self.order_id = order_id
        self.product = product
        self.guild_id = guild_id

    paypay_link = discord.ui.TextInput(
        label='PayPayリンクを入力してください',
        placeholder='https://paypay.ne.jp/app/v2/p2p-api/getP2PLinkInfo?link_key=...',
        style=discord.TextStyle.long,
        required=True,
        max_length=500
    )

    async def on_submit(self, interaction: discord.Interaction):
        paypay_link = self.paypay_link.value.strip()

        # PayPayリンクの簡単な検証
        if not paypay_link.startswith('https://paypay.ne.jp/'):
            await interaction.response.send_message(
                "❌ 無効なPayPayリンクです。正しいPayPayリンクを入力してください。",
                ephemeral=True
            )
            return

        # 管理者チャンネルに通知を送信
        vending_machine = bot.get_guild_vending_machine(self.guild_id)
        for admin_channel_id in vending_machine['admin_channels']:
            try:
                admin_channel = bot.get_channel(admin_channel_id)
                if admin_channel:
                    await self.send_admin_notification(admin_channel, self.order_id, interaction.user, self.product, paypay_link)
            except Exception as e:
                print(f"管理者チャンネル通知エラー: {e}")

        # ユーザーに確認メッセージを送信
        purchase_embed = discord.Embed(
            title="🛒 商品注文完了",
            description=f"**{self.product['name']}** の注文を受け付けました。\n"
                       f"管理者が確認次第、DMで商品をお送ります！",
            color=get_random_color()
        )

        purchase_embed.add_field(name="注文ID", value=f"#{self.order_id}", inline=True)
        purchase_embed.add_field(name="金額", value=f"¥{self.product['price']:,}", inline=True)
        purchase_embed.add_field(name="ステータス", value="支払い確認待ち", inline=True)

        await interaction.response.send_message(embed=purchase_embed, ephemeral=True)

    async def send_admin_notification(self, channel, order_id, user, product, paypay_link):
        """管理者チャンネルに通知を送信"""
        admin_embed = discord.Embed(
            title="💰 新規注文通知",
            description=f"新しい商品注文が入りました。",
            color=get_random_color(),
            timestamp=discord.utils.utcnow()
        )

        admin_embed.add_field(name="注文ID", value=f"#{order_id}", inline=True)
        admin_embed.add_field(name="購入者", value=f"{user.mention}\n({user.name})", inline=True)
        admin_embed.add_field(name="商品", value=product['name'], inline=True)
        admin_embed.add_field(name="金額", value=f"¥{product['price']:,}", inline=True)
        admin_embed.add_field(name="PayPayリンク", value=f"[支払いリンク]({paypay_link})", inline=False)

        admin_embed.set_thumbnail(url=user.display_avatar.url)

        view = AdminApprovalView(order_id)
        await channel.send(embed=admin_embed, view=view)

class AdminApprovalView(discord.ui.View):
    def __init__(self, order_id):
        super().__init__(timeout=3600)  # 1時間でタイムアウト
        self.order_id = str(order_id)
        self.guild_id = None  # 初期化時は不明、ボタンクリック時に設定

    @discord.ui.button(label='商品送信', style=discord.ButtonStyle.success)
    async def approve_order(self, interaction: discord.Interaction, button: discord.ui.Button):
        """注文を承認して商品を送信"""
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "この操作は管理者のみです！",
                ephemeral=True
            )
            return

        self.guild_id = interaction.guild.id
        vending_machine = bot.get_guild_vending_machine(self.guild_id)
        order = vending_machine['orders'].get(self.order_id)
        if not order:
            await interaction.response.send_message(
                "注文が見つかりません！",
                ephemeral=True
            )
            return

        if order['status'] == 'completed':
            await interaction.response.send_message(
                "この注文は既に商品が送信済みです！",
                ephemeral=True
            )
            return
        elif order['status'] == 'cancelled':
            await interaction.response.send_message(
                "この注文はキャンセルされました！",
                ephemeral=True
            )
            return
        elif order['status'] != 'pending_payment':
            await interaction.response.send_message(
                "この注文は既に終了しました！",
                ephemeral=True
            )
            return

        await interaction.response.send_modal(ProductDeliveryModal(self.order_id))

    @discord.ui.button(label='注文キャンセル', style=discord.ButtonStyle.danger)
    async def reject_order(self, interaction: discord.Interaction, button: discord.ui.Button):
        """注文をキャンセル"""
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "この操作は管理者のみです！",
                ephemeral=True
            )
            return

        guild_id = interaction.guild.id
        vending_machine = bot.get_guild_vending_machine(guild_id)
        order = vending_machine['orders'].get(self.order_id)
        if not order:
            await interaction.response.send_message(
                "注文が見つからないよ！",
                ephemeral=True
            )
            return

        # 注文をキャンセル状態に（在庫は注文時に減らしていないので戻す必要なし）
        order['status'] = 'cancelled'

        # 購入者にDM送信
        try:
            user = await bot.fetch_user(int(order['user_id']))
            if user:
                cancel_embed = discord.Embed(
                    title="注文キャンセル",
                    description=f"注文 #{self.order_id} がキャンセルされたよ！\n"
                               f"何かあったら鯖主へgo!",
                    color=get_random_color()
                )
                await user.send(embed=cancel_embed)
        except Exception as e:
            print(f"キャンセル通知DM送信エラー: {e}")

        # 管理者メッセージを更新
        cancel_embed = discord.Embed(
            title="注文のキャンセルが完了しました！",
            description=f"注文 #{self.order_id} をキャンセルしました！\n実行者: {interaction.user.mention}",
            color=get_random_color()
        )

        await interaction.response.edit_message(embed=cancel_embed, view=None)
        print(f'{interaction.user.name} が注文 #{self.order_id} をキャンセルしました！')

class ProductDeliveryModal(discord.ui.Modal, title='商品送信'):
    def __init__(self, order_id):
        super().__init__()
        self.order_id = order_id
        self.guild_id = None  # 送信時に設定

    async def send_achievement_notification(self, guild_id, order_id, buyer, product, processor):
        """実績チャンネルに購入実績を送信"""
        try:
            vending_machine = bot.get_guild_vending_machine(guild_id)
            achievement_channel_id = vending_machine.get('achievement_channel')

            if not achievement_channel_id:
                return  # 実績チャンネルが設定されていない場合はスキップ

            achievement_channel = bot.get_channel(achievement_channel_id)
            if not achievement_channel:
                print(f"実績チャンネルが見つかりません！: {achievement_channel_id}")
                return

            # 実績Embedを作成
            achievement_embed = discord.Embed(
                title="購入実績",
                description="新しい商品が購入されました！",
                color=get_random_color(),
                timestamp=discord.utils.utcnow()
            )

            achievement_embed.add_field(
                name="購入者",
                value=f"{buyer.mention}\n({buyer.display_name})",
                inline=True
            )

            achievement_embed.add_field(
                name="商品",
                value=f"**{product['name']}**\n{product['description'][:50]}{'...' if len(product['description']) > 50 else ''}",
                inline=True
            )

            achievement_embed.add_field(
                name="価格",
                value=f"¥{product['price']:,}",
                inline=True
            )

            achievement_embed.add_field(
                name="注文ID",
                value=f"#{order_id}",
                inline=True
            )

            achievement_embed.add_field(
                name="管理者 ",
                value=f"{processor.mention}\n({processor.display_name})",
                inline=True
            )

            achievement_embed.add_field(
                name="残り在庫",
                value=f"{product['stock']}個",
                inline=True
            )

            achievement_embed.set_thumbnail(url=buyer.display_avatar.url)
            achievement_embed.set_footer(
                text="半自動販売機システム",
                icon_url=bot.user.display_avatar.url if bot.user else None
            )

            await achievement_channel.send(embed=achievement_embed)
            print(f"実績チャンネルに購入通知を送信しました: 注文 #{order_id}")

        except Exception as e:
            print(f"実績通知送信エラー: {e}")

    async def on_submit(self, interaction: discord.Interaction):
        guild_id = interaction.guild.id
        vending_machine = bot.get_guild_vending_machine(guild_id)
        order = vending_machine['orders'].get(self.order_id)
        if not order:
            await interaction.response.send_message(
                "注文が見つかりません！",
                ephemeral=True
            )
            return

        product_id = order['product_id']
        product = vending_machine['products'].get(product_id)
        if not product:
            await interaction.response.send_message(
                "商品が見つかりません！",
                ephemeral=True
            )
            return

        # 在庫から1つ取り出す
        inventory = product.get('inventory', [])
        if not inventory:
            await interaction.response.send_message(
                "この商品の在庫がありません！",
                ephemeral=True
            )
            return

        # 最初の在庫アイテムを取り出し、在庫から削除
        item_content = inventory.pop(0)
        product['stock'] = len(inventory)  # 在庫数を更新

        # 注文を完了状態にし、処理者情報を記録
        order['status'] = 'completed'
        order['processed_by'] = str(interaction.user.id)
        order['processed_at'] = time.time()

        # 購入者にDMで商品を送信
        try:
            user = await bot.fetch_user(int(order['user_id']))

            delivery_embed = discord.Embed(
                title="商品お届け",
                description=f"ご注文いただいた商品をお届けします！",
                color=get_random_color(),
                timestamp=discord.utils.utcnow()
            )

            delivery_embed.add_field(name="注文ID", value=f"#{self.order_id}", inline=True)
            delivery_embed.add_field(name="商品名", value=product['name'], inline=True)
            delivery_embed.add_field(name="商品内容", value=item_content, inline=False)

            await user.send(embed=delivery_embed)

            # 管理者メッセージを更新（ボタンを無効化）
            success_embed = discord.Embed(
                title="✅ 商品送信完了",
                description=f"注文 #{self.order_id} の商品を送信しました！\n"
                           f"実行者: {interaction.user.mention}\n"
                           f"残り在庫: {product['stock']}個\n"
                           f"ステータス: 送信完了",
                color=get_random_color(),
                timestamp=discord.utils.utcnow()
            )

            success_embed.add_field(
                name="商品内容", 
                value=item_content[:100] + ("..." if len(item_content) > 100 else ""), 
                inline=False
            )

            await interaction.response.edit_message(embed=success_embed, view=None)
            print(f'{interaction.user.name} が注文 #{self.order_id} の商品を送信しました (残り在庫: {product["stock"]}個)')

            # 実績チャンネルに通知を送信
            await self.send_achievement_notification(guild_id, self.order_id, user, product, interaction.user)

        except discord.Forbidden:
            # 送信失敗時は在庫を戻す
            inventory.insert(0, item_content)
            product['stock'] = len(inventory)
            order['status'] = 'pending_payment'  # ステータスを戻す

            await interaction.response.send_message(
                "dmに送信できませんでした、dmが無効の可能性があります！\n"
                "在庫は減っていません",
                ephemeral=True
            )
        except Exception as e:
            # 送信失敗時は在庫を戻す
            inventory.insert(0, item_content)
            product['stock'] = len(inventory)
            order['status'] = 'pending_payment'  # ステータスを戻す

            await interaction.response.send_message(
                f"商品送信中にエラーが発生しました！: {str(e)}\n"
                "在庫は元に戻されました。",
                ephemeral=True
            )
            print(f"商品送信エラー！: {e}")

class TicketPanelView(discord.ui.View):
    def __init__(self, category: discord.CategoryChannel = None):
        super().__init__(timeout=None)
        self.category = category

    @discord.ui.button(label='チケットを作成', style=discord.ButtonStyle.primary, emoji='🎫')
    async def create_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        """チケットチャンネルを作成"""
        guild = interaction.guild
        user = interaction.user

        # 既存のチケットチャンネルをチェック
        existing_ticket = None
        for channel in guild.channels:
            if (isinstance(channel, discord.TextChannel) and 
                channel.name.startswith(f'ticket-{user.name.lower()}') and
                user in [member for member in channel.members]):
                existing_ticket = channel
                break

        if existing_ticket:
            await interaction.response.send_message(
                f"※ 既にチケットチャンネル {existing_ticket.mention} が存在します!",
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        try:
            # チケットチャンネル名を生成
            channel_name = f"ticket-{user.name.lower()}-{user.discriminator}"

            # 権限設定
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(read_messages=False),
                user: discord.PermissionOverwrite(
                    read_messages=True,
                    send_messages=True,
                    attach_files=True,
                    embed_links=True,
                    read_message_history=True
                ),
                guild.me: discord.PermissionOverwrite(
                    read_messages=True,
                    send_messages=True,
                    manage_messages=True,
                    embed_links=True,
                    attach_files=True,
                    read_message_history=True
                )
            }

            # 管理者権限を持つロールにも権限を付与
            for role in guild.roles:
                if role.permissions.administrator:
                    overwrites[role] = discord.PermissionOverwrite(
                        read_messages=True,
                        send_messages=True,
                        manage_messages=True,
                        embed_links=True,
                        attach_files=True,
                        read_message_history=True
                    )

            # チケットチャンネルを作成
            ticket_channel = await guild.create_text_channel(
                name=channel_name,
                category=self.category,
                overwrites=overwrites,
                topic=f"{user.display_name} のチケット"
            )

            # チケット情報のEmbed作成
            ticket_embed = discord.Embed(
                title="🎫 チケット",
                description=f"{user.mention} さん、\n"
                           f"要件を言ってお待ちください。",
                color=get_random_color(),
                timestamp=discord.utils.utcnow()
            )

            ticket_embed.add_field(
                name=" チケット作成者",
                value=f"{user.display_name} ({user.mention})",
                inline=True
            )

            ticket_embed.add_field(
                name=" 作成日時",
                value=discord.utils.format_dt(discord.utils.utcnow(), style='F'),
                inline=True
            )

            ticket_embed.add_field(
                name=" 注意事項",
                value="• スタッフがくるまでお待ちください\n"
                     "• 間違えて開いたのであれば「チケット閉じる」ボタンを押してください\n",
                inline=False
            )

            # チケット管理ボタン
            ticket_view = TicketManageView(user.id)

            # チケットチャンネルにメッセージを送信
            await ticket_channel.send(
                content=f"{user.mention}",
                embed=ticket_embed,
                view=ticket_view
            )

            # 作成完了メッセージ
            await interaction.followup.send(
                f"チケットチャンネル {ticket_channel.mention} を作成したよ！",
                ephemeral=True
            )

            print(f'🎫 {user.name} がチケットチャンネル「{channel_name}」を作成しました')

        except Exception as e:
            await interaction.followup.send(
                f"チケット作成中にエラーが発生しました！: {str(e)}",
                ephemeral=True
            )
            print(f"Ticket creation error: {e}")

class GiveawayView(discord.ui.View):
    def __init__(self, prize, winners, end_time, host_id):
        super().__init__(timeout=None)
        self.prize = prize
        self.winners = winners
        self.end_time = end_time
        self.host_id = host_id
        self.participants = set()  # 参加者のユーザーIDセット

    @discord.ui.button(label='参加', style=discord.ButtonStyle.success)
    async def join_giveaway(self, interaction: discord.Interaction, button: discord.ui.Button):
        """ギブアウェイに参加"""
        user_id = interaction.user.id

        # 既に終了しているかチェック
        if discord.utils.utcnow() >= self.end_time:
            await interaction.response.send_message(
                "このgiveawayは既に終了してるよ！",
                ephemeral=True
            )
            return

        # 主催者は参加できない
        if user_id == self.host_id:
            await interaction.response.send_message(
                "主催者は自分のgiveawayに参加できません！",
                ephemeral=True
            )
            return

        # 既に参加しているかチェック
        if user_id in self.participants:
            await interaction.response.send_message(
                "既にこのgiveawayに参加しています。",
                ephemeral=True
            )
            return

        # 参加者リストに追加
        self.participants.add(user_id)

        # 確認メッセージ
        join_embed = discord.Embed(
            title="✅ ギブアウェイ参加完了",
            description=f"**景品:** {self.prize}\n"
                       f"ギブアウェイに参加しました！\n\n"
                       f"抽選開始は {discord.utils.format_dt(self.end_time, style='R')} です！\n"
                       f"参加してね！",
            color=get_random_color()
        )

        join_embed.add_field(
            name="現在の参加者数",
            value=f"{len(self.participants)}人",
            inline=True
        )

        join_embed.add_field(
            name="当選数",
            value=f"{self.winners}人",
            inline=True
        )

        await interaction.response.send_message(embed=join_embed, ephemeral=True)
        print(f'{interaction.user.name} がgiveaway「{self.prize}」に参加しました!（現在{len(self.participants)}人参加）')

    @discord.ui.button(label='参加者数確認', style=discord.ButtonStyle.secondary)
    async def check_participants(self, interaction: discord.Interaction, button: discord.ui.Button):
        """参加者数を確認"""
        remaining_time = self.end_time - discord.utils.utcnow()

        if remaining_time.total_seconds() <= 0:
            status = "終了済み"
            time_info = "このgiveawayは終了しています！"
        else:
            status = "開催中"
            time_info = f"終了まで {discord.utils.format_dt(self.end_time, style='R')}"

        info_embed = discord.Embed(
            title="giveaway情報",
            description=f"**景品:** {self.prize}\n"
                       f"**ステータス:** {status}\n"
                       f"**{time_info}**",
            color=get_random_color()
        )

        info_embed.add_field(
            name="参加者数",
            value=f"{len(self.participants)}人",
            inline=True
        )

        info_embed.add_field(
            name="当選者数",
            value=f"{self.winners}人",
            inline=True
        )
        await interaction.response.send_message(embed=info_embed, ephemeral=True)

class TicketManageView(discord.ui.View):
    def __init__(self, creator_id: int):
        super().__init__(timeout=None)
        self.creator_id = creator_id

    @discord.ui.button(label='チケット閉じる！', style=discord.ButtonStyle.danger, emoji='🔒')
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        """チケットを閉じる"""
        user = interaction.user
        channel = interaction.channel

        # 確認メッセージを表示
        confirm_embed = discord.Embed(
            title="⚠️ チケットを閉じる確認",
            description="このチケットを閉じますか？\n\n"
                       "**注意:** チケットが削除されるよ！\n"
                       "買った情報は事前に保存してください。",
            color=get_random_color()
        )

        confirm_view = TicketCloseConfirmView(self.creator_id)
        await interaction.response.send_message(
            embed=confirm_embed,
            view=confirm_view,
            ephemeral=True
        )

    @discord.ui.button(label='参加者追加', style=discord.ButtonStyle.secondary)
    async def add_user_to_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        """チケットに他のユーザーを追加"""
        await interaction.response.send_modal(AddUserModal())

class TicketCloseConfirmView(discord.ui.View):
    def __init__(self, creator_id: int):
        super().__init__(timeout=30)
        self.creator_id = creator_id

    @discord.ui.button(label='チケットを閉じる', style=discord.ButtonStyle.danger)
    async def confirm_close(self, interaction: discord.Interaction, button: discord.ui.Button):
        """チケット閉じることを確認"""
        user = interaction.user
        channel = interaction.channel

        await interaction.response.defer()

        try:
            # 閉じる前にログメッセージを送信
            close_embed = discord.Embed(
                title="チケット閉じられました！",
                description=f"チケットが {user.mention} によって閉じられました！",
                color=get_random_color(),
                timestamp=discord.utils.utcnow()
            )

            await channel.send(embed=close_embed)

            # 5秒後にチャンネルを削除
            await asyncio.sleep(5)
            await channel.delete(reason=f"チケット閉じられました - {user.name}")

            print(f'{user.name} がチケットチャンネル「{channel.name}」を閉じました！')

        except Exception as e:
            await interaction.followup.send(
                f"チケットを閉じる際にエラーが発生しました！: {str(e)}",
                ephemeral=True
            )
            print(f"Ticket close error: {e}")

    @discord.ui.button(label='キャンセル', style=discord.ButtonStyle.secondary)
    async def cancel_close(self, interaction: discord.Interaction, button: discord.ui.Button):
        """チケット閉じることをキャンセル"""
        cancel_embed = discord.Embed(
            title="キャンセル",
            description="チケットを閉じる操作がキャンセルされました！",
            color=get_random_color()
        )
        await interaction.response.edit_message(embed=cancel_embed, view=None)

class AddUserModal(discord.ui.Modal, title='ユーザーをチケットに追加'):
    def __init__(self):
        super().__init__()

    user_input = discord.ui.TextInput(
        label='ユーザーID または ユーザー名',
        placeholder='追加するユーザーのIDまたは名前を入力してね！',
        required=True,
        max_length=100
    )

    async def on_submit(self, interaction: discord.Interaction):
        channel = interaction.channel
        guild = interaction.guild
        user_input = self.user_input.value.strip()

        # ユーザーを検索
        target_user = None

        # IDで検索を試行
        if user_input.isdigit():
            try:
                target_user = await guild.fetch_member(int(user_input))
            except discord.NotFound:
                pass

        # 名前で検索を試行
        if not target_user:
            target_user = discord.utils.get(guild.members, name=user_input)
            if not target_user:
                target_user = discord.utils.get(guild.members, display_name=user_input)

        if not target_user:
            await interaction.response.send_message(
                f"❌ ユーザー「{user_input}」が見つかりませんでした。",
                ephemeral=True
            )
            return

        # 既にチャンネルにアクセス権限があるかチェック
        if target_user in channel.members:
            await interaction.response.send_message(
                f"❌ {target_user.mention} は既にこのチケットを見れます。",
                ephemeral=True
            )
            return

        try:
            # ユーザーに権限を付与
            await channel.set_permissions(
                target_user,
                read_messages=True,
                send_messages=True,
                attach_files=True,
                embed_links=True,
                read_message_history=True
            )

            # 追加通知
            add_embed = discord.Embed(
                title="➕ ユーザー追加",
                description=f"{target_user.mention} がチケットに追加されました。",
                color=get_random_color(),
                timestamp=discord.utils.utcnow()
            )

            await channel.send(embed=add_embed)
            await interaction.response.send_message(
                f"✅ {target_user.mention} をチケットに追加しました。",
                ephemeral=True
            )

            print(f'{interaction.user.name} が {target_user.name} をチケット「{channel.name}」に追加しました')

        except Exception as e:
            await interaction.response.send_message(
                f"❌ ユーザー追加中にエラーが発生しました: {str(e)}",
                ephemeral=True
            )
            print(f"Add user to ticket error: {e}")

# プレフィックスコマンド
@bot.command(name='call')
@commands.has_permissions(administrator=True)
async def call_authenticated_users(ctx, *, message: str = None):
    """認証済みユーザーを呼び出す"""
    guild_id = ctx.guild.id

    if guild_id not in bot.authenticated_users or not bot.authenticated_users[guild_id]:
        await ctx.send("このサーバーには認証済みユーザーがいません。")
        return

    # 認証済みユーザーのメンションリストを作成
    mentions = []
    valid_users = []

    for user_id in bot.authenticated_users[guild_id]:
        # まずキャッシュから検索
        member = ctx.guild.get_member(int(user_id))
        if not member:
            # キャッシュにない場合は直接フェッチを試行
            try:
                member = await ctx.guild.fetch_member(int(user_id))
                print(f'認証済みメンバーを発見！: {member.display_name}')
            except discord.NotFound:
                print(f'メンバーがサーバーから退出ました！: User ID {user_id}')
                continue
            except Exception as e:
                print(f'メンバー取得エラー: {e}')
                continue

        if member:
            mentions.append(member.mention)
            valid_users.append(user_id)

    # 無効なユーザーを認証済みリストから削除
    if len(valid_users) != len(bot.authenticated_users[guild_id]):
        bot.authenticated_users[guild_id] = valid_users
        print(f'無効なユーザーを削除しました！')

    if not mentions:
        await ctx.send("認証済みユーザーがサーバーに見つかりませんでした！。")
        return

    # メンションメッセージを作成
    mention_text = " ".join(mentions)
    call_message = f"{mention_text}"

    if message:
        call_message += f"\n\n**メッセージ:** {message}"

    # 文字数制限を確認（Discordの制限は2000文字）
    if len(call_message) > 2000:
        # 長すぎる場合は分割して送信
        chunks = [call_message[i:i+2000] for i in range(0, len(call_message), 2000)]
        for chunk in chunks:
            await ctx.send(chunk)
    else:
        await ctx.send(call_message)

    print(f'{ctx.author.name} が {len(mentions)} 人の認証済みユーザーを呼び出しました！')

@bot.command(name='nuke')
@commands.has_permissions(administrator=True)
async def nuke_channel(ctx):
    """チャンネルを権限を引き継いで再生成する"""
    channel = ctx.channel

    # 確認メッセージを送信
    confirm_embed = discord.Embed(
        title="nuke確認",
        description=f"チャンネル「{channel.name}」をnukeしますか？\n\n"
                   "nukeされます：\n",
        color=get_random_color()
    )

    # 確認ボタンを作成
    view = NukeConfirmView(ctx.author.id)
    message = await ctx.send(embed=confirm_embed, view=view)
    view.message = message

class NukeConfirmView(discord.ui.View):
    def __init__(self, author_id):
        super().__init__(timeout=30)
        self.author_id = author_id
        self.message = None

    @discord.ui.button(label='実行', style=discord.ButtonStyle.danger)
    async def confirm_nuke(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("このボタンはコマンド実行者のみが使用できます。", ephemeral=True)
            return

        await interaction.response.defer()

        channel = interaction.channel
        guild = interaction.guild

        try:
            # チャンネル情報を保存
            channel_name = channel.name
            channel_topic = getattr(channel, 'topic', None)
            channel_category = channel.category
            channel_position = channel.position
            channel_nsfw = getattr(channel, 'nsfw', False)
            channel_slowmode = getattr(channel, 'slowmode_delay', 0)

            # 権限設定を保存
            overwrites = {}
            for target, overwrite in channel.overwrites.items():
                overwrites[target] = overwrite

            # 新しいチャンネルを作成
            new_channel = await guild.create_text_channel(
                name=channel_name,
                topic=channel_topic,
                category=channel_category,
                position=channel_position,
                nsfw=channel_nsfw,
                slowmode_delay=channel_slowmode,
                overwrites=overwrites
            )

            # 成功メッセージを新しいチャンネルに送信
            success_embed = discord.Embed(
                title="✅ チャンネル再生成完了",
                description=f"チャンネル「{channel_name}」がnukeされました！",
                color=get_random_color()
            )
            await new_channel.send(embed=success_embed)

            # 元のチャンネルを削除
            await channel.delete()

            print(f'{interaction.user.name} がチャンネル「{channel_name}」をnukeしました')

        except Exception as e:
            error_embed = discord.Embed(
                title="❌ エラー",
                description=f"チャンネルのnuke中にエラーが発生したよ！：\n{str(e)}",
                color=get_random_color()
            )
            await interaction.followup.send(embed=error_embed, ephemeral=True)
            print(f'Nuke command error: {e}')

    @discord.ui.button(label='キャンセル', style=discord.ButtonStyle.secondary)
    async def cancel_nuke(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("コマンド実行者のみできます！", ephemeral=True)
            return

        cancel_embed = discord.Embed(
            title="キャンセル",
            description="nukeがキャンセルされたよ！",
            color=get_random_color()
        )
        await interaction.response.edit_message(embed=cancel_embed, view=None)

    async def on_timeout(self):
        if self.message:
            timeout_embed = discord.Embed(
                title="タイムアウト",
                description="確認がタイムアウトしました。チャンネルの再生成はキャンセルされました。",
                color=get_random_color()
            )
            try:
                await self.message.edit(embed=timeout_embed, view=None)
            except:
                pass

async def start_bot_with_retry():
    """レート制限対策でボットを起動"""
    max_retries = 5
    base_delay = 60  # 1分

    for attempt in range(max_retries):
        try:
            print(f"Discord OAuth認証ボット（複数サーバー対応）を開始しています... (試行 {attempt + 1}/{max_retries})")
            await bot.start(BOT_TOKEN)
            break
        except discord.HTTPException as e:
            if e.status == 429:  # レート制限エラー
                if attempt < max_retries - 1:
                    # 指数バックオフ + ランダム要素でリトライ
                    delay = base_delay * (2 ** attempt) + random.uniform(1, 10)
                    print(f"❌ レート制限エラーが発生しました。{delay:.1f}秒後に再試行します...")
                    await asyncio.sleep(delay)
                else:
                    print("❌ 最大試行回数に達しました。しばらく時間をおいてから再度実行してください。")
                    raise
            else:
                print(f"❌ HTTPエラーが発生しました: {e}")
                raise
        except Exception as e:
            print(f"❌ 予期しないエラーが発生しました: {e}")
            if attempt < max_retries - 1:
                delay = base_delay + random.uniform(1, 10)
                print(f"⚠️ {delay:.1f}秒後に再試行します...")
                await asyncio.sleep(delay)
            else:
                raise

def main():
    if not BOT_TOKEN:
        print("DISCORD_BOT_TOKEN環境変数が設定されていません")
        return

    if not CLIENT_ID or not CLIENT_SECRET:
        print("DISCORD_CLIENT_IDまたはDISCORD_CLIENT_SECRET環境変数が設定されていません")
        return

    try:
        asyncio.run(start_bot_with_retry())
    except KeyboardInterrupt:
        print("ボットが手動で停止されました")
    except Exception as e:
        print(f"ボットの起動に失敗しました: {e}")
        print("しばらく時間をおいてから再度実行してください")

if __name__ == "__main__":
    main()
