
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
        intents.message_content = True 
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
        
        # 定期削除タイマーを管理
        self.scheduled_nukes = {}  # {channel_id: asyncio.Task}
        
        # 半自動販売機システム
        self.vending_machine = {
            'products': {},  # {product_id: {'name': str, 'price': int, 'description': str, 'stock': int}}
            'orders': {},    # {order_id: {'user_id': str, 'product_id': str, 'status': str, 'channel_id': int}}
            'admin_channels': set(),  # 管理者チャンネルのIDセット
            'next_order_id': 1
        }
    
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
                print(f'サーバー {guild.name} の参加日時を記録しました')
        
        # プレイ中ステータスを設定
        await self.update_status()
        
        # スラッシュコマンドを同期
        try:
            synced = await self.tree.sync()
            print(f'{len(synced)}個のスラッシュコマンドを同期しました')
        except Exception as e:
            print(f'スラッシュコマンドの同期エラー: {e}')
        
        # 2週間制限チェックタスクを開始
        asyncio.create_task(self.check_guild_expiry())
        
        # Webサーバーを開始
        await self.start_web_server()
    
    async def update_status(self):
        """プレイ中ステータスを更新"""
        try:
            guild_count = len(self.guilds)
            activity = discord.Game(name=f"{guild_count}個のサーバーで活動中")
            await self.change_presence(activity=activity, status=discord.Status.online)
            print(f'ステータスを更新: {guild_count}個のサーバーで活動中')
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
                                    title="⏰ Bot利用期間終了のお知らせ",
                                    description="当Botの2週間利用期間が終了しました。\n"
                                               "引き続きご利用をご希望の場合は、再度招待してください。\n\n"
                                               "ご利用いただき、ありがとうございました！",
                                    color=0xff6b6b,
                                    timestamp=discord.utils.utcnow()
                                )
                                await notification_channel.send(embed=expire_embed)
                        except Exception as e:
                            print(f'退出通知送信エラー (Guild {guild.name}): {e}')
                        
                        # サーバーから退出
                        await guild.leave()
                        print(f'✅ 2週間制限により {guild.name} から退出しました')
                        
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
                    title="🎉 ご招待ありがとうございます！",
                    description=f"**{guild.name}** へようこそ！\n\n"
                               "当Botは以下の機能を提供します：\n"
                               "• OAuth認証システム\n"
                               "• レベル・ランキング機能\n"
                               "• チャンネル管理機能\n"
                               "• 半自動販売機システム\n"
                               "• チケットシステム\n\n"
                               "⚠️ **重要：このBotは2週間の利用制限があります**\n"
                               "2週間後に自動的にサーバーから退出します。",
                    color=0x00ff00,
                    timestamp=discord.utils.utcnow()
                )
                
                expire_date = discord.utils.utcnow() + timedelta(days=14)
                welcome_embed.add_field(
                    name="📅 利用期限",
                    value=discord.utils.format_dt(expire_date, style='F'),
                    inline=True
                )
                
                welcome_embed.add_field(
                    name="🔧 設定方法",
                    value="管理者は `/role` コマンドで認証システムを設定できます",
                    inline=True
                )
                
                await welcome_channel.send(embed=welcome_embed)
                print(f'歓迎メッセージを {guild.name} に送信しました')
                
        except Exception as e:
            print(f'歓迎メッセージ送信エラー ({guild.name}): {e}')
    
    async def on_guild_remove(self, guild):
        """サーバーから退出した時の処理"""
        print(f'サーバーから退出しました: {guild.name} (ID: {guild.id})')
        
        # 関連データをクリーンアップ
        if guild.id in self.guild_configs:
            del self.guild_configs[guild.id]
        if guild.id in self.guild_join_dates:
            del self.guild_join_dates[guild.id]
        if guild.id in self.authenticated_users:
            del self.authenticated_users[guild.id]
        if guild.id in self.user_levels:
            del self.user_levels[guild.id]
        
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
                description=f"{message.author.mention} さんがレベル {new_level} になりました！",
                color=0xffd700
            )
            level_up_embed.add_field(
                name="前のレベル",
                value=f"レベル {old_level}",
                inline=True
            )
            level_up_embed.add_field(
                name="新しいレベル", 
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
                print(f'✅ サーバーへの追加が成功しました')
                
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
                    print(f'🎭 ロール付与を試行中...')
                    role_assigned = await self.assign_role(user_id, guild_id, role_id)
                    
                    # 認証済みユーザーとして記録
                    if guild_id not in self.authenticated_users:
                        self.authenticated_users[guild_id] = []
                    if user_id not in self.authenticated_users[guild_id]:
                        self.authenticated_users[guild_id].append(user_id)
                        print(f'認証済みユーザーに追加: {username} (User ID: {user_id}, Guild ID: {guild_id})')
                else:
                    print(f'❌ サーバー参加の確認に失敗しました')
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
            print(f'OAuth処理エラー: {e}')
            return web.Response(text=f'処理中にエラーが発生しました: {e}', status=500)
    
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
                            print(f'🎉 新しいメンバーがサーバーに参加しました')
                            return True
                        elif status in [200, 204]:
                            print(f'ℹ️ ユーザーは既にサーバーのメンバーでした')
                            return True
                        elif status == 403:
                            print(f'❌ 権限エラー: サーバーに参加する権限がありません')
                            print(f'📄 詳細: {error_text}')
                            return False
                        elif status == 400:
                            print(f'❌ リクエストエラー: 無効なリクエストまたは制限に達しています')
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
        print(f'🎭 ロール付与を API 経由で実行中: User {user_id}, Role {role_id}, Guild {guild_id}')
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
                title="🕐 定期チャンネル削除完了",
                description=f"チャンネル「{channel_name}」が定期削除により再生成されました。\n実行者: {author_name}",
                color=0x00ff00,
                timestamp=discord.utils.utcnow()
            )
            await new_channel.send(embed=success_embed)
            
            print(f'定期nuke実行: チャンネル「{channel_name}」が再生成されました (実行者: {author_name})')
            
        except asyncio.CancelledError:
            print(f'定期nuke がキャンセルされました: {channel.name}')
        except Exception as e:
            print(f'定期nuke エラー: {e}')
        finally:
            # タスクリストから削除
            if channel.id in self.scheduled_nukes:
                del self.scheduled_nukes[channel.id]

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
            label='認証',
            style=discord.ButtonStyle.link,
            emoji='🔗',
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
        placeholder="付与するロールを選択してください...",
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
            description="リンクボタンから登録して認証完了",
            color=0x00ff00
        )
        
        await interaction.response.send_message(embed=embed, view=view)

# スラッシュコマンド
@bot.tree.command(name='role', description='認証メッセージを送信します')
@app_commands.describe(role='付与するロール', channel='送信するチャンネル（省略した場合は現在のチャンネル）')
@app_commands.default_permissions(administrator=True)
async def role_slash(interaction: discord.Interaction, role: discord.Role, channel: discord.TextChannel = None):
    """認証メッセージを指定したチャンネルに送信"""
    target_channel = channel or interaction.channel
    
    view = AuthLinkView(interaction.guild, role)
    
    embed = discord.Embed(
        title="こんにちは！",
        description="リンクボタンから登録して認証完了",
        color=0x00ff00
    )
    
    await target_channel.send(embed=embed, view=view)
    await interaction.response.send_message(f"認証メッセージを {target_channel.mention} に送信しました", ephemeral=True)

@bot.tree.command(name='call', description='他のサーバーで認証済みのユーザーを現在のサーバーに直接参加させます')
@app_commands.default_permissions(administrator=True)
async def call_slash(interaction: discord.Interaction):
    """他のサーバーで認証済みのユーザーを現在のサーバーに直接参加させる"""
    
    # mume_dayoユーザーのみ使用可能
    if interaction.user.name != 'mume_dayo':
        await interaction.response.send_message("❌ このコマンドはmume_dayoユーザーのみが使用できます。", ephemeral=True)
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
                    print(f'ユーザー {user_id} のアクセストークンが見つかりません')
                
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
                print(f'🚀 ユーザー {user.name} を {current_guild.name} に参加させています...')
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
        await interaction.followup.send("📭 追加対象の認証済みユーザーが見つかりませんでした。\n\n"
                                       "• 他のサーバーで認証済みのユーザーがいません\n"
                                       "• アクセストークンが保存されているユーザーがいません", ephemeral=True)
        return
    
    result_message = f"🎯 **サーバー参加結果** - {current_guild.name}\n\n"
    
    if already_member_count > 0:
        result_message += f"📋 既にメンバー: {already_member_count}人\n"
    
    if added_count > 0:
        result_message += f"✅ 新規参加: {added_count}人\n"
    
    if failed_count > 0:
        result_message += f"❌ 参加失敗: {failed_count}人\n"
    
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
        description=f"チャンネル「{channel.name}」を再生成しますか？\n\n"
                   "この操作により：\n"
                   "• 現在のチャンネルは削除されます\n" 
                   "• 同じ名前と権限で新しいチャンネルが作成されます\n"
                   "• すべてのメッセージ履歴が削除されます\n\n"
                   "**この操作は取り消せません！**",
        color=0xff0000
    )
    
    # 確認ボタンを作成
    view = NukeConfirmView(interaction.user.id)
    await interaction.response.send_message(embed=confirm_embed, view=view, ephemeral=True)
    view.message = await interaction.original_response()

@bot.tree.command(name='level', description='自分または指定したユーザーのレベル情報を表示します')
@app_commands.describe(user='レベル情報を表示するユーザー（省略した場合は自分）')
async def level_slash(interaction: discord.Interaction, user: discord.Member = None):
    """ユーザーのレベル情報を表示"""
    target_user = user or interaction.user
    guild_id = interaction.guild.id
    user_id = str(target_user.id)
    
    # ユーザーのレベルデータを取得
    if guild_id not in bot.user_levels or user_id not in bot.user_levels[guild_id]:
        embed = discord.Embed(
            title="📊 レベル情報",
            description=f"{target_user.display_name} さんはまだメッセージを送信していません。",
            color=0x95a5a6
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
        color=0x3498db
    )
    
    embed.set_author(
        name=target_user.display_name,
        icon_url=target_user.display_avatar.url
    )
    
    embed.add_field(
        name="🏆 現在のレベル",
        value=f"レベル {current_level}",
        inline=True
    )
    
    embed.add_field(
        name="⭐ 合計XP",
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
        value=f"{progress_bar}\n{xp_progress}/{xp_required_for_next} XP ({xp_needed} XP 必要)",
        inline=False
    )
    
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name='ranking', description='サーバーのレベルランキングを表示します')
@app_commands.describe(page='表示するページ（1ページ10人）')
async def ranking_slash(interaction: discord.Interaction, page: int = 1):
    """サーバーのレベルランキングを表示"""
    guild_id = interaction.guild.id
    
    if guild_id not in bot.user_levels or not bot.user_levels[guild_id]:
        embed = discord.Embed(
            title="🏆 レベルランキング",
            description="このサーバーにはまだランキングデータがありません。",
            color=0x95a5a6
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
            description=f"無効なページ番号です。1～{total_pages}の範囲で指定してください。",
            color=0xe74c3c
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    # 表示するユーザーを取得
    start_index = (page - 1) * users_per_page
    end_index = start_index + users_per_page
    page_users = sorted_users[start_index:end_index]
    
    embed = discord.Embed(
        title="🏆 レベルランキング",
        description=f"ページ {page}/{total_pages}",
        color=0xffd700
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
        embed.description = "このページには表示するユーザーがいません。"
    
    embed.set_footer(text=f"合計 {len(sorted_users)} 人のユーザー")
    
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name='log', description='サーバーログを別のサーバーに転送します')
@app_commands.describe(
    webhook_url='ログ送信先のウェブフックURL',
    message='送信するメッセージ',
    username='送信者として表示する名前（省略可）',
    avatar_url='送信者のアバターURL（省略可）'
)
@app_commands.default_permissions(administrator=True)
async def log_slash(
    interaction: discord.Interaction,
    webhook_url: str,
    message: str,
    username: str = None,
    avatar_url: str = None
):
    """ウェブフックを使用してサーバーログを別のサーバーに送信"""
    
    await interaction.response.defer(ephemeral=True)
    
    try:
        # 現在のサーバー情報を取得
        current_guild = interaction.guild
        current_user = interaction.user
        current_time = discord.utils.utcnow()
        
        # ログメッセージの準備
        log_embed = discord.Embed(
            title="📋 サーバーログ",
            color=0x3498db,
            timestamp=current_time
        )
        
        log_embed.add_field(
            name="送信者",
            value=f"{current_user.display_name} ({current_user.name})",
            inline=True
        )
        
        log_embed.add_field(
            name="サーバー",
            value=f"{current_guild.name} (ID: {current_guild.id})",
            inline=True
        )
        
        log_embed.add_field(
            name="チャンネル",
            value=f"#{interaction.channel.name}",
            inline=True
        )
        
        log_embed.add_field(
            name="メッセージ",
            value=message,
            inline=False
        )
        
        # ウェブフックデータの準備
        webhook_data = {
            "embeds": [log_embed.to_dict()]
        }
        
        # カスタムユーザー名とアバターが指定されている場合
        if username:
            webhook_data["username"] = username
        else:
            webhook_data["username"] = f"{current_user.display_name} (via {current_guild.name})"
            
        if avatar_url:
            webhook_data["avatar_url"] = avatar_url
        elif current_user.avatar:
            webhook_data["avatar_url"] = current_user.avatar.url
        
        # ウェブフックでメッセージを送信
        async with aiohttp.ClientSession() as session:
            async with session.post(webhook_url, json=webhook_data) as response:
                if response.status in [200, 204]:
                    await interaction.followup.send(
                        "✅ ログが正常に送信されました！",
                        ephemeral=True
                    )
                    print(f"📤 {current_user.name} がサーバーログを送信しました: {message[:50]}...")
                else:
                    error_text = await response.text()
                    await interaction.followup.send(
                        f"❌ ログの送信に失敗しました。ステータスコード: {response.status}\n"
                        f"エラー: {error_text[:200]}",
                        ephemeral=True
                    )
                    print(f"❌ ウェブフック送信エラー ({response.status}): {error_text}")
                    
    except Exception as e:
        await interaction.followup.send(
            f"❌ ログ送信中にエラーが発生しました: {str(e)}",
            ephemeral=True
        )
        print(f"❌ Log command error: {e}")

@bot.tree.command(name='timenuke', description='指定した時間後にチャンネルを自動削除・再生成します')
@app_commands.describe(time='削除までの時間（d:h:m:s形式、例: 0:1:30:0 = 1時間30分後）')
@app_commands.default_permissions(administrator=True)
async def timenuke_slash(interaction: discord.Interaction, time: str):
    """指定した時間後にチャンネルを削除・再生成する"""
    channel = interaction.channel
    
    # 既に定期削除が設定されているかチェック
    if channel.id in bot.scheduled_nukes:
        await interaction.response.send_message(
            "❌ このチャンネルには既に定期削除が設定されています。\n"
            "`/timecancel` でキャンセルしてから再設定してください。",
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
            "❌ 最小時間は1分です。",
            ephemeral=True
        )
        return
    
    if delay_seconds > 604800:  # 7日間
        await interaction.response.send_message(
            "❌ 最大時間は7日間です。",
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
        title="⏰ 定期削除を設定しました",
        description=f"チャンネル「{channel.name}」を**{time_remaining}後**に削除・再生成します。",
        color=0xff9500,
        timestamp=discord.utils.utcnow()
    )
    
    confirm_embed.add_field(
        name="🕐 実行予定時刻",
        value=discord.utils.format_dt(execution_time, style='F'),
        inline=True
    )
    
    confirm_embed.add_field(
        name="👤 実行者",
        value=interaction.user.mention,
        inline=True
    )
    
    confirm_embed.add_field(
        name="ℹ️ 注意",
        value="`/timecancel` でキャンセル可能です",
        inline=False
    )
    
    await interaction.response.send_message(embed=confirm_embed)
    
    print(f'{interaction.user.name} がチャンネル「{channel.name}」に{time_remaining}後の定期削除を設定しました')

@bot.tree.command(name='timecancel', description='設定されている定期削除をキャンセルします')
@app_commands.default_permissions(administrator=True)
async def timecancel_slash(interaction: discord.Interaction):
    """定期削除をキャンセルする"""
    channel = interaction.channel
    
    if channel.id not in bot.scheduled_nukes:
        await interaction.response.send_message(
            "❌ このチャンネルには定期削除が設定されていません。",
            ephemeral=True
        )
        return
    
    # タスクをキャンセル
    task = bot.scheduled_nukes[channel.id]
    task.cancel()
    del bot.scheduled_nukes[channel.id]
    
    cancel_embed = discord.Embed(
        title="🚫 定期削除をキャンセルしました",
        description=f"チャンネル「{channel.name}」の定期削除がキャンセルされました。",
        color=0x95a5a6,
        timestamp=discord.utils.utcnow()
    )
    
    cancel_embed.add_field(
        name="👤 キャンセル実行者",
        value=interaction.user.mention,
        inline=True
    )
    
    await interaction.response.send_message(embed=cancel_embed)
    
    print(f'{interaction.user.name} がチャンネル「{channel.name}」の定期削除をキャンセルしました')

@bot.tree.command(name='delete', description='指定した数のメッセージを削除します')
@app_commands.describe(
    amount='削除するメッセージ数（1-100）',
    member='特定のメンバーのメッセージのみ削除（省略可）'
)
@app_commands.default_permissions(manage_messages=True)
async def delete_slash(interaction: discord.Interaction, amount: int, member: discord.Member = None):
    """指定した数のメッセージを削除する"""
    
    # 削除数の制限
    if amount < 1 or amount > 100:
        await interaction.response.send_message(
            "❌ 削除数は1から100までの範囲で指定してください。",
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
                title="🗑️ メッセージ削除完了",
                description=f"{member.mention} のメッセージを **{deleted_count}件** 削除しました。",
                color=0xe74c3c,
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
                title="🗑️ メッセージ削除完了",
                description=f"最新のメッセージを **{deleted_count}件** 削除しました。",
                color=0xe74c3c,
                timestamp=discord.utils.utcnow()
            )
        
        result_embed.add_field(
            name="👤 実行者",
            value=interaction.user.mention,
            inline=True
        )
        
        result_embed.add_field(
            name="📍 チャンネル",
            value=f"#{channel.name}",
            inline=True
        )
        
        await interaction.followup.send(embed=result_embed, ephemeral=True)
        
        print(f'{interaction.user.name} が {channel.name} で {deleted_count}件のメッセージを削除しました (対象: {member.name if member else "全員"})')
        
    except Exception as e:
        await interaction.followup.send(
            f"❌ メッセージ削除中にエラーが発生しました: {str(e)}",
            ephemeral=True
        )
        print(f"Delete command error: {e}")

@bot.tree.command(name='vending_setup', description='販売機の管理者チャンネルを設定します')
@app_commands.default_permissions(administrator=True)
async def vending_setup_slash(interaction: discord.Interaction):
    """販売機の管理者チャンネルを設定"""
    channel_id = interaction.channel.id
    
    if channel_id in bot.vending_machine['admin_channels']:
        await interaction.response.send_message(
            "❌ このチャンネルは既に管理者チャンネルとして設定されています。",
            ephemeral=True
        )
        return
    
    bot.vending_machine['admin_channels'].add(channel_id)
    
    setup_embed = discord.Embed(
        title="⚙️ 販売機管理者チャンネル設定完了",
        description=f"このチャンネルが販売機の管理者チャンネルとして設定されました。\n"
                   f"商品が購入されると、ここに承認依頼が送信されます。",
        color=0x00ff00,
        timestamp=discord.utils.utcnow()
    )
    
    await interaction.response.send_message(embed=setup_embed)
    print(f'{interaction.user.name} がチャンネル {interaction.channel.name} を販売機管理者チャンネルに設定しました')

@bot.tree.command(name='add_product', description='販売機に商品を追加します')
@app_commands.describe(
    product_id='商品ID（英数字）',
    name='商品名',
    price='価格（円）',
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
            "❌ 商品IDは英数字とアンダースコアのみ使用可能です。",
            ephemeral=True
        )
        return
    
    if price < 1:
        await interaction.response.send_message(
            "❌ 価格は1円以上で設定してください。",
            ephemeral=True
        )
        return
    
    if stock < 0:
        await interaction.response.send_message(
            "❌ 在庫数は0以上で設定してください。",
            ephemeral=True
        )
        return
    
    bot.vending_machine['products'][product_id] = {
        'name': name,
        'price': price,
        'description': description,
        'stock': stock
    }
    
    product_embed = discord.Embed(
        title="✅ 商品追加完了",
        description=f"商品「{name}」が販売機に追加されました。",
        color=0x00ff00
    )
    
    product_embed.add_field(name="商品ID", value=product_id, inline=True)
    product_embed.add_field(name="価格", value=f"¥{price:,}", inline=True)
    product_embed.add_field(name="在庫", value=f"{stock}個", inline=True)
    product_embed.add_field(name="説明", value=description, inline=False)
    
    await interaction.response.send_message(embed=product_embed)
    print(f'{interaction.user.name} が商品「{name}」を販売機に追加しました')

@bot.tree.command(name='vending_panel', description='販売機パネルを設置します')
@app_commands.default_permissions(administrator=True)
async def vending_panel_slash(interaction: discord.Interaction):
    """販売機パネルを設置"""
    if not bot.vending_machine['products']:
        await interaction.response.send_message(
            "❌ 販売する商品がありません。先に `/add_product` で商品を追加してください。",
            ephemeral=True
        )
        return
    
    if not bot.vending_machine['admin_channels']:
        await interaction.response.send_message(
            "❌ 管理者チャンネルが設定されていません。先に `/vending_setup` で設定してください。",
            ephemeral=True
        )
        return
    
    panel_embed = discord.Embed(
        title="🛒 半自動販売機",
        description="購入したい商品を選択してください。\n"
                   "購入後、管理者の承認を経てDMで商品をお届けします。",
        color=0x3498db
    )
    
    # 商品一覧を表示
    product_list = ""
    for product_id, product in bot.vending_machine['products'].items():
        stock_status = f"在庫: {product['stock']}個" if product['stock'] > 0 else "❌ 在庫切れ"
        product_list += f"**{product['name']}** - ¥{product['price']:,}\n{product['description']}\n{stock_status}\n\n"
    
    panel_embed.add_field(
        name="📦 商品一覧",
        value=product_list,
        inline=False
    )
    
    panel_embed.set_footer(text="購入には PayPay での支払いが必要です")
    
    view = VendingMachineView()
    await interaction.response.send_message(embed=panel_embed, view=view)
    print(f'{interaction.user.name} が販売機パネルを設置しました')

@bot.tree.command(name='ticket_panel', description='チケット作成パネルを設置します')
@app_commands.describe(
    title='パネルのタイトル（省略可）',
    description='パネルの説明文（省略可）',
    category='チケットを作成するカテゴリ（省略可）'
)
@app_commands.default_permissions(administrator=True)
async def ticket_panel_slash(
    interaction: discord.Interaction,
    title: str = "サポートチケット",
    description: str = "何かお困りのことがありましたら、下のボタンをクリックしてチケットを作成してください。",
    category: discord.CategoryChannel = None
):
    """チケット作成パネルを設置"""
    
    # パネル用のEmbed作成
    panel_embed = discord.Embed(
        title=f"🎫 {title}",
        description=f"🎫\n\n{description}",
        color=0x3498db
    )
    
    panel_embed.add_field(
        name="📋 使い方",
        value="ボタンを押してね",
        inline=False
    )
    
    # チケット作成ボタン付きのViewを作成
    view = TicketPanelView(category)
    
    await interaction.response.send_message(embed=panel_embed, view=view)
    
    print(f'{interaction.user.name} がチケットパネルを設置しました')

class VendingMachineView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        
        # 商品選択用のセレクトメニューを作成
        options = []
        for product_id, product in bot.vending_machine['products'].items():
            if product['stock'] > 0:
                options.append(discord.SelectOption(
                    label=f"{product['name']} - ¥{product['price']:,}",
                    value=product_id,
                    description=product['description'][:100],
                    emoji="📦"
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
        product = bot.vending_machine['products'].get(product_id)
        
        if not product:
            await interaction.response.send_message(
                "❌ 選択された商品が見つかりません。",
                ephemeral=True
            )
            return
        
        if product['stock'] <= 0:
            await interaction.response.send_message(
                "❌ この商品は在庫切れです。",
                ephemeral=True
            )
            return
        
        # 注文IDを生成
        order_id = bot.vending_machine['next_order_id']
        bot.vending_machine['next_order_id'] += 1
        
        # 注文を記録
        bot.vending_machine['orders'][str(order_id)] = {
            'user_id': str(interaction.user.id),
            'product_id': product_id,
            'status': 'pending_payment',
            'channel_id': interaction.channel.id,
            'timestamp': time.time()
        }
        
        # 在庫を減らす
        bot.vending_machine['products'][product_id]['stock'] -= 1
        
        # PayPayリンクを生成（実際のPayPayリンクに置き換えてください）
        paypay_link = f"https://paypay.ne.jp/app/v2/p2p-api/getP2PLinkInfo?link_key=EXAMPLE_{order_id}"
        
        # 管理者チャンネルに通知を送信
        for admin_channel_id in bot.vending_machine['admin_channels']:
            try:
                admin_channel = bot.get_channel(admin_channel_id)
                if admin_channel:
                    await self.send_admin_notification(admin_channel, order_id, interaction.user, product, paypay_link)
            except Exception as e:
                print(f"管理者チャンネル通知エラー: {e}")
        
        # ユーザーに確認メッセージを送信
        purchase_embed = discord.Embed(
            title="🛒 商品注文完了",
            description=f"**{product['name']}** の注文を受け付けました。\n"
                       f"管理者が支払いを確認次第、DMで商品をお送りします。",
            color=0xffa500
        )
        
        purchase_embed.add_field(name="注文ID", value=f"#{order_id}", inline=True)
        purchase_embed.add_field(name="金額", value=f"¥{product['price']:,}", inline=True)
        purchase_embed.add_field(name="ステータス", value="支払い確認待ち", inline=True)
        
        await interaction.response.send_message(embed=purchase_embed, ephemeral=True)
        print(f'{interaction.user.name} が商品「{product["name"]}」を注文しました (注文ID: {order_id})')
    
    async def send_admin_notification(self, channel, order_id, user, product, paypay_link):
        """管理者チャンネルに通知を送信"""
        admin_embed = discord.Embed(
            title="💰 新規注文通知",
            description=f"新しい商品注文が入りました。",
            color=0xff6b6b,
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
    
    @discord.ui.button(label='商品送信', style=discord.ButtonStyle.success, emoji='✅')
    async def approve_order(self, interaction: discord.Interaction, button: discord.ui.Button):
        """注文を承認して商品を送信"""
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "❌ この操作は管理者のみ実行できます。",
                ephemeral=True
            )
            return
        
        order = bot.vending_machine['orders'].get(self.order_id)
        if not order:
            await interaction.response.send_message(
                "❌ 注文が見つかりません。",
                ephemeral=True
            )
            return
        
        if order['status'] != 'pending_payment':
            await interaction.response.send_message(
                "❌ この注文は既に処理済みです。",
                ephemeral=True
            )
            return
        
        await interaction.response.send_modal(ProductDeliveryModal(self.order_id))
    
    @discord.ui.button(label='注文キャンセル', style=discord.ButtonStyle.danger, emoji='❌')
    async def reject_order(self, interaction: discord.Interaction, button: discord.ui.Button):
        """注文をキャンセル"""
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "❌ この操作は管理者のみ実行できます。",
                ephemeral=True
            )
            return
        
        order = bot.vending_machine['orders'].get(self.order_id)
        if not order:
            await interaction.response.send_message(
                "❌ 注文が見つかりません。",
                ephemeral=True
            )
            return
        
        # 在庫を戻す
        product_id = order['product_id']
        if product_id in bot.vending_machine['products']:
            bot.vending_machine['products'][product_id]['stock'] += 1
        
        # 注文をキャンセル状態に
        order['status'] = 'cancelled'
        
        # 購入者にDM送信
        try:
            user = await bot.fetch_user(int(order['user_id']))
            if user:
                cancel_embed = discord.Embed(
                    title="❌ 注文キャンセル",
                    description=f"注文 #{self.order_id} がキャンセルされました。\n"
                               f"ご不明な点がございましたら、管理者にお問い合わせください。",
                    color=0xff0000
                )
                await user.send(embed=cancel_embed)
        except Exception as e:
            print(f"キャンセル通知DM送信エラー: {e}")
        
        # 管理者メッセージを更新
        cancel_embed = discord.Embed(
            title="❌ 注文キャンセル完了",
            description=f"注文 #{self.order_id} をキャンセルしました。\n実行者: {interaction.user.mention}",
            color=0xff0000
        )
        
        await interaction.response.edit_message(embed=cancel_embed, view=None)
        print(f'{interaction.user.name} が注文 #{self.order_id} をキャンセルしました')

class ProductDeliveryModal(discord.ui.Modal, title='商品送信'):
    def __init__(self, order_id):
        super().__init__()
        self.order_id = order_id
    
    product_content = discord.ui.TextInput(
        label='商品内容',
        placeholder='DMで送信する商品内容を入力してください...',
        style=discord.TextStyle.long,
        required=True,
        max_length=2000
    )
    
    async def on_submit(self, interaction: discord.Interaction):
        order = bot.vending_machine['orders'].get(self.order_id)
        if not order:
            await interaction.response.send_message(
                "❌ 注文が見つかりません。",
                ephemeral=True
            )
            return
        
        # 注文を完了状態に
        order['status'] = 'completed'
        
        # 購入者にDMで商品を送信
        try:
            user = await bot.fetch_user(int(order['user_id']))
            product = bot.vending_machine['products'][order['product_id']]
            
            delivery_embed = discord.Embed(
                title="📦 商品お届け",
                description=f"ご注文いただいた商品をお届けします。",
                color=0x00ff00,
                timestamp=discord.utils.utcnow()
            )
            
            delivery_embed.add_field(name="注文ID", value=f"#{self.order_id}", inline=True)
            delivery_embed.add_field(name="商品名", value=product['name'], inline=True)
            delivery_embed.add_field(name="商品内容", value=self.product_content.value, inline=False)
            
            await user.send(embed=delivery_embed)
            
            # 管理者メッセージを更新
            success_embed = discord.Embed(
                title="✅ 商品送信完了",
                description=f"注文 #{self.order_id} の商品を送信しました。\n実行者: {interaction.user.mention}",
                color=0x00ff00
            )
            
            await interaction.response.edit_message(embed=success_embed, view=None)
            print(f'{interaction.user.name} が注文 #{self.order_id} の商品を送信しました')
            
        except discord.Forbidden:
            await interaction.response.send_message(
                "❌ 購入者のDMに送信できませんでした。DMが無効になっている可能性があります。",
                ephemeral=True
            )
        except Exception as e:
            await interaction.response.send_message(
                f"❌ 商品送信中にエラーが発生しました: {str(e)}",
                ephemeral=True
            )
            print(f"商品送信エラー: {e}")

class TicketPanelView(discord.ui.View):
    def __init__(self, category: discord.CategoryChannel = None):
        super().__init__(timeout=None)
        self.category = category
    
    @discord.ui.button(label='チケット作成', style=discord.ButtonStyle.primary, emoji='🎫')
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
                f"❌ 既にチケットチャンネル {existing_ticket.mention} が存在します。",
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
                topic=f"{user.display_name} のサポートチケット"
            )
            
            # チケット情報のEmbed作成
            ticket_embed = discord.Embed(
                title="🎫 サポートチケット",
                description=f"{user.mention} さん、サポートチケットへようこそ！\n"
                           f"お困りのことがございましたら、こちらでお気軽にご相談ください。",
                color=0x00ff00,
                timestamp=discord.utils.utcnow()
            )
            
            ticket_embed.add_field(
                name="📝 チケット作成者",
                value=f"{user.display_name} ({user.mention})",
                inline=True
            )
            
            ticket_embed.add_field(
                name="🕒 作成日時",
                value=discord.utils.format_dt(discord.utils.utcnow(), style='F'),
                inline=True
            )
            
            ticket_embed.add_field(
                name="ℹ️ 注意事項",
                value="• スタッフが対応するまでお待ちください\n"
                     "• 問題が解決したら「チケット閉じる」ボタンを押してください\n"
                     "• 不適切な利用は禁止されています",
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
                f"✅ チケットチャンネル {ticket_channel.mention} を作成しました！",
                ephemeral=True
            )
            
            print(f'🎫 {user.name} がチケットチャンネル「{channel_name}」を作成しました')
            
        except Exception as e:
            await interaction.followup.send(
                f"❌ チケット作成中にエラーが発生しました: {str(e)}",
                ephemeral=True
            )
            print(f"Ticket creation error: {e}")

class TicketManageView(discord.ui.View):
    def __init__(self, creator_id: int):
        super().__init__(timeout=None)
        self.creator_id = creator_id
    
    @discord.ui.button(label='チケット閉じる', style=discord.ButtonStyle.danger, emoji='🔒')
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        """チケットを閉じる"""
        user = interaction.user
        channel = interaction.channel
        
        # チケット作成者または管理者のみがチケットを閉じることができる
        if (user.id != self.creator_id and 
            not user.guild_permissions.administrator):
            await interaction.response.send_message(
                "❌ チケットを閉じる権限がありません。",
                ephemeral=True
            )
            return
        
        # 確認メッセージを表示
        confirm_embed = discord.Embed(
            title="⚠️ チケットを閉じる確認",
            description="このチケットを閉じますか？\n\n"
                       "**注意:** この操作により、チケットチャンネルが削除されます。\n"
                       "必要な情報は事前に保存してください。",
            color=0xff6b6b
        )
        
        confirm_view = TicketCloseConfirmView(self.creator_id)
        await interaction.response.send_message(
            embed=confirm_embed,
            view=confirm_view,
            ephemeral=True
        )
    
    @discord.ui.button(label='参加者追加', style=discord.ButtonStyle.secondary, emoji='➕')
    async def add_user_to_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        """チケットに他のユーザーを追加"""
        await interaction.response.send_modal(AddUserModal())

class TicketCloseConfirmView(discord.ui.View):
    def __init__(self, creator_id: int):
        super().__init__(timeout=30)
        self.creator_id = creator_id
    
    @discord.ui.button(label='チケットを閉じる', style=discord.ButtonStyle.danger, emoji='🗑️')
    async def confirm_close(self, interaction: discord.Interaction, button: discord.ui.Button):
        """チケット閉じることを確認"""
        user = interaction.user
        channel = interaction.channel
        
        if (user.id != self.creator_id and 
            not user.guild_permissions.administrator):
            await interaction.response.send_message(
                "❌ チケットを閉じる権限がありません。",
                ephemeral=True
            )
            return
        
        await interaction.response.defer()
        
        try:
            # 閉じる前にログメッセージを送信
            close_embed = discord.Embed(
                title="🔒 チケット閉じられました",
                description=f"チケットが {user.mention} によって閉じられました。",
                color=0x95a5a6,
                timestamp=discord.utils.utcnow()
            )
            
            await channel.send(embed=close_embed)
            
            # 5秒後にチャンネルを削除
            await asyncio.sleep(5)
            await channel.delete(reason=f"チケット閉じられました - {user.name}")
            
            print(f'{user.name} がチケットチャンネル「{channel.name}」を閉じました')
            
        except Exception as e:
            await interaction.followup.send(
                f"❌ チケットを閉じる際にエラーが発生しました: {str(e)}",
                ephemeral=True
            )
            print(f"Ticket close error: {e}")
    
    @discord.ui.button(label='キャンセル', style=discord.ButtonStyle.secondary, emoji='❌')
    async def cancel_close(self, interaction: discord.Interaction, button: discord.ui.Button):
        """チケット閉じることをキャンセル"""
        cancel_embed = discord.Embed(
            title="キャンセル",
            description="チケットを閉じる操作がキャンセルされました。",
            color=0x95a5a6
        )
        await interaction.response.edit_message(embed=cancel_embed, view=None)

class AddUserModal(discord.ui.Modal, title='ユーザーをチケットに追加'):
    def __init__(self):
        super().__init__()
    
    user_input = discord.ui.TextInput(
        label='ユーザーID または ユーザー名',
        placeholder='追加するユーザーのIDまたは名前を入力してください',
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
                f"❌ {target_user.mention} は既にこのチケットにアクセスできます。",
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
                color=0x3498db,
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
                print(f'フェッチで認証済みメンバーを発見: {member.display_name}')
            except discord.NotFound:
                print(f'認証済みメンバーがサーバーから退出しています: User ID {user_id}')
                continue
            except Exception as e:
                print(f'メンバーフェッチエラー: {e}')
                continue
        
        if member:
            mentions.append(member.mention)
            valid_users.append(user_id)
    
    # 無効なユーザーを認証済みリストから削除
    if len(valid_users) != len(bot.authenticated_users[guild_id]):
        bot.authenticated_users[guild_id] = valid_users
        print(f'無効なユーザーを削除しました')
    
    if not mentions:
        await ctx.send("認証済みユーザーがサーバーに見つかりません。")
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
    
    print(f'{ctx.author.name} が {len(mentions)} 人の認証済みユーザーを呼び出しました')

@bot.command(name='nuke')
@commands.has_permissions(administrator=True)
async def nuke_channel(ctx):
    """チャンネルを権限を引き継いで再生成する"""
    channel = ctx.channel
    
    # 確認メッセージを送信
    confirm_embed = discord.Embed(
        title="⚠️ チャンネル再生成の確認",
        description=f"チャンネル「{channel.name}」を再生成しますか？\n\n"
                   "この操作により：\n"
                   "• 現在のチャンネルは削除されます\n" 
                   "• 同じ名前と権限で新しいチャンネルが作成されます\n"
                   "• すべてのメッセージ履歴が削除されます\n\n"
                   "**この操作は取り消せません！**",
        color=0xff0000
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
    
    @discord.ui.button(label='実行', style=discord.ButtonStyle.danger, emoji='💥')
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
                description=f"チャンネル「{channel_name}」が正常に再生成されました。",
                color=0x00ff00
            )
            await new_channel.send(embed=success_embed)
            
            # 元のチャンネルを削除
            await channel.delete()
            
            print(f'{interaction.user.name} がチャンネル「{channel_name}」を再生成しました')
            
        except Exception as e:
            error_embed = discord.Embed(
                title="❌ エラー",
                description=f"チャンネルの再生成中にエラーが発生しました：\n{str(e)}",
                color=0xff0000
            )
            await interaction.followup.send(embed=error_embed, ephemeral=True)
            print(f'Nuke command error: {e}')
    
    @discord.ui.button(label='キャンセル', style=discord.ButtonStyle.secondary, emoji='❌')
    async def cancel_nuke(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("このボタンはコマンド実行者のみが使用できます。", ephemeral=True)
            return
        
        cancel_embed = discord.Embed(
            title="キャンセル",
            description="チャンネルの再生成がキャンセルされました。",
            color=0x808080
        )
        await interaction.response.edit_message(embed=cancel_embed, view=None)
    
    async def on_timeout(self):
        if self.message:
            timeout_embed = discord.Embed(
                title="タイムアウト",
                description="確認がタイムアウトしました。チャンネルの再生成はキャンセルされました。",
                color=0x808080
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
