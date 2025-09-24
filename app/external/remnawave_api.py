import asyncio
import base64
import json
import logging
import ssl
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional, Union
from urllib.parse import urljoin, urlparse

import aiohttp

from app.utils.link_utils import normalize_subscription_url

logger = logging.getLogger(__name__)


class UserStatus(Enum):
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"
    LIMITED = "LIMITED"
    EXPIRED = "EXPIRED"


class TrafficLimitStrategy(Enum):
    NO_RESET = "NO_RESET"
    DAY = "DAY"
    WEEK = "WEEK"
    MONTH = "MONTH"


@dataclass
class RemnaWaveUser:
    uuid: str
    short_uuid: str
    username: str
    status: UserStatus
    used_traffic_bytes: int
    lifetime_used_traffic_bytes: int 
    traffic_limit_bytes: int
    traffic_limit_strategy: TrafficLimitStrategy
    expire_at: datetime
    telegram_id: Optional[int]
    email: Optional[str]
    hwid_device_limit: Optional[int]
    description: Optional[str]
    tag: Optional[str]
    subscription_url: str
    active_internal_squads: List[Dict[str, str]]
    created_at: datetime
    updated_at: datetime
    sub_last_user_agent: Optional[str] = None
    sub_last_opened_at: Optional[datetime] = None
    online_at: Optional[datetime] = None
    sub_revoked_at: Optional[datetime] = None
    last_traffic_reset_at: Optional[datetime] = None
    trojan_password: Optional[str] = None
    vless_uuid: Optional[str] = None
    ss_password: Optional[str] = None
    first_connected_at: Optional[datetime] = None
    last_triggered_threshold: int = 0


@dataclass
class RemnaWaveInternalSquad:
    uuid: str
    name: str
    members_count: int
    inbounds_count: int
    inbounds: List[Dict[str, Any]]


@dataclass
class RemnaWaveNode:
    uuid: str
    name: str
    address: str
    country_code: str
    is_connected: bool
    is_disabled: bool
    is_node_online: bool
    is_xray_running: bool
    users_online: Optional[int]
    traffic_used_bytes: Optional[int]
    traffic_limit_bytes: Optional[int]


@dataclass
class SubscriptionInfo:
    is_found: bool
    user: Optional[Dict[str, Any]]
    links: List[str]
    ss_conf_links: Dict[str, str]
    subscription_url: str
    happ: Optional[Dict[str, str]]


class RemnaWaveAPIError(Exception):
    def __init__(self, message: str, status_code: int = None, response_data: dict = None):
        self.message = message
        self.status_code = status_code
        self.response_data = response_data
        super().__init__(self.message)


class RemnaWaveAPI:
    
    def __init__(self, base_url: str, api_key: str, secret_key: Optional[str] = None, 
                 username: Optional[str] = None, password: Optional[str] = None):
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        self.secret_key = secret_key
        self.username = username
        self.password = password
        self.session: Optional[aiohttp.ClientSession] = None
        self.authenticated = False
        
    def _detect_connection_type(self) -> str:
        parsed = urlparse(self.base_url)
        
        local_hosts = [
            'localhost', '127.0.0.1', 'remnawave', 
            'remnawave-backend', 'app', 'api'
        ]
        
        if parsed.hostname in local_hosts:
            return "local"
            
        if parsed.hostname:
            if (parsed.hostname.startswith('192.168.') or 
                parsed.hostname.startswith('10.') or 
                parsed.hostname.startswith('172.') or
                parsed.hostname.endswith('.local')):
                return "local"
        
        return "external"

    def _prepare_auth_headers(self) -> Dict[str, str]:
        headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'X-Forwarded-Proto': 'https',
            'X-Forwarded-For': '127.0.0.1',
            'X-Real-IP': '127.0.0.1'
        }
        
        if self.username and self.password:
            import base64
            credentials = f"{self.username}:{self.password}"
            encoded_credentials = base64.b64encode(credentials.encode()).decode()
            headers['X-Api-Key'] = f"Basic {encoded_credentials}"
            logger.debug("Используем Basic Auth в X-Api-Key заголовке")
        else:
            headers['X-Api-Key'] = self.api_key
            logger.debug("Используем API ключ в X-Api-Key заголовке")
        
        headers['Authorization'] = f'Bearer {self.api_key}'
        
        return headers
        
    async def __aenter__(self):
        conn_type = self._detect_connection_type()
        
        logger.info(f"Подключение к Remnawave: {self.base_url} (тип: {conn_type})")
            
        headers = self._prepare_auth_headers() 
        
        cookies = None
        if self.secret_key:
            if ':' in self.secret_key:
                key_name, key_value = self.secret_key.split(':', 1)
                cookies = {key_name: key_value}
                logger.debug(f"Используем куки: {key_name}=***")
            else:
                cookies = {self.secret_key: self.secret_key}
                logger.debug(f"Используем куки: {self.secret_key}=***")
        
        connector_kwargs = {}
        
        if conn_type == "local":
            logger.debug("Используют локальные заголовки proxy")
            headers.update({
                'X-Forwarded-Host': 'localhost',
                'Host': 'localhost'
            })
            
            if self.base_url.startswith('https://'):
                ssl_context = ssl.create_default_context()
                ssl_context.check_hostname = False
                ssl_context.verify_mode = ssl.CERT_NONE
                connector_kwargs['ssl'] = ssl_context
                logger.debug("SSL проверка отключена для локального HTTPS")
                
        elif conn_type == "external":
            logger.debug("Используют внешнее подключение с полной SSL проверкой")
            pass
            
        connector = aiohttp.TCPConnector(**connector_kwargs)
        
        session_kwargs = {
            'timeout': aiohttp.ClientTimeout(total=30),
            'headers': headers,
            'connector': connector
        }
        
        if cookies:
            session_kwargs['cookies'] = cookies
            
        self.session = aiohttp.ClientSession(**session_kwargs)
        self.authenticated = True 
                
        return self
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()
            
    async def _make_request(
        self, 
        method: str, 
        endpoint: str, 
        data: Optional[Dict] = None,
        params: Optional[Dict] = None
    ) -> Dict:
        if not self.session:
            raise RemnaWaveAPIError("Session not initialized. Use async context manager.")
            
        url = f"{self.base_url}{endpoint}"
        
        try:
            kwargs = {
                'url': url,
                'params': params
            }
            
            if data:
                kwargs['json'] = data
                
            async with self.session.request(method, **kwargs) as response:
                response_text = await response.text()
                
                try:
                    response_data = json.loads(response_text) if response_text else {}
                except json.JSONDecodeError:
                    response_data = {'raw_response': response_text}
                
                if response.status >= 400:
                    error_message = response_data.get('message', f'HTTP {response.status}')
                    logger.error(f"API Error {response.status}: {error_message}")
                    logger.error(f"Response: {response_text[:500]}")
                    raise RemnaWaveAPIError(
                        error_message, 
                        response.status, 
                        response_data
                    )
                    
                return response_data
                
        except aiohttp.ClientError as e:
            logger.error(f"Request failed: {e}")
            raise RemnaWaveAPIError(f"Request failed: {str(e)}")
    
    
    async def create_user(
        self,
        username: str,
        expire_at: datetime,
        status: UserStatus = UserStatus.ACTIVE,
        traffic_limit_bytes: int = 0,
        traffic_limit_strategy: TrafficLimitStrategy = TrafficLimitStrategy.NO_RESET,
        telegram_id: Optional[int] = None,
        email: Optional[str] = None,
        hwid_device_limit: Optional[int] = None,
        description: Optional[str] = None,
        tag: Optional[str] = None,
        active_internal_squads: Optional[List[str]] = None
    ) -> RemnaWaveUser:
        data = {
            'username': username,
            'status': status.value,
            'expireAt': expire_at.isoformat(),
            'trafficLimitBytes': traffic_limit_bytes,
            'trafficLimitStrategy': traffic_limit_strategy.value
        }
        
        if telegram_id:
            data['telegramId'] = telegram_id
        if email:
            data['email'] = email
        if hwid_device_limit:
            data['hwidDeviceLimit'] = hwid_device_limit
        if description:
            data['description'] = description
        if tag:
            data['tag'] = tag
        if active_internal_squads:
            data['activeInternalSquads'] = active_internal_squads
            
        response = await self._make_request('POST', '/api/users', data)
        return self._parse_user(response['response'])
    
    async def get_user_by_uuid(self, uuid: str) -> Optional[RemnaWaveUser]:
        try:
            response = await self._make_request('GET', f'/api/users/{uuid}')
            return self._parse_user(response['response'])
        except RemnaWaveAPIError as e:
            if e.status_code == 404:
                return None
            raise
    
    async def get_user_by_telegram_id(self, telegram_id: int) -> List[RemnaWaveUser]:
        try:
            response = await self._make_request('GET', f'/api/users/by-telegram-id/{telegram_id}')
            users_data = response.get('response', [])
            if not users_data:
                return []
            return [self._parse_user(user) for user in users_data]
        except RemnaWaveAPIError as e:
            if e.status_code == 404:
                return []
            raise
    
    async def get_user_by_username(self, username: str) -> Optional[RemnaWaveUser]:
        try:
            response = await self._make_request('GET', f'/api/users/by-username/{username}')
            return self._parse_user(response['response'])
        except RemnaWaveAPIError as e:
            if e.status_code == 404:
                return None
            raise
    
    async def update_user(
        self,
        uuid: str,
        status: Optional[UserStatus] = None,
        traffic_limit_bytes: Optional[int] = None,
        traffic_limit_strategy: Optional[TrafficLimitStrategy] = None,
        expire_at: Optional[datetime] = None,
        telegram_id: Optional[int] = None,
        email: Optional[str] = None,
        hwid_device_limit: Optional[int] = None,
        description: Optional[str] = None,
        tag: Optional[str] = None,
        active_internal_squads: Optional[List[str]] = None
    ) -> RemnaWaveUser:
        data = {'uuid': uuid}
        
        if status:
            data['status'] = status.value
        if traffic_limit_bytes is not None:
            data['trafficLimitBytes'] = traffic_limit_bytes
        if traffic_limit_strategy:
            data['trafficLimitStrategy'] = traffic_limit_strategy.value
        if expire_at:
            data['expireAt'] = expire_at.isoformat()
        if telegram_id is not None:
            data['telegramId'] = telegram_id
        if email is not None:
            data['email'] = email
        if hwid_device_limit is not None:
            data['hwidDeviceLimit'] = hwid_device_limit
        if description is not None:
            data['description'] = description
        if tag is not None:
            data['tag'] = tag
        if active_internal_squads is not None:
            data['activeInternalSquads'] = active_internal_squads
            
        response = await self._make_request('PATCH', '/api/users', data)
        return self._parse_user(response['response'])
    
    async def delete_user(self, uuid: str) -> bool:
        response = await self._make_request('DELETE', f'/api/users/{uuid}')
        return response['response']['isDeleted']
    
    async def enable_user(self, uuid: str) -> RemnaWaveUser:
        response = await self._make_request('POST', f'/api/users/{uuid}/actions/enable')
        return self._parse_user(response['response'])
    
    async def disable_user(self, uuid: str) -> RemnaWaveUser:
        response = await self._make_request('POST', f'/api/users/{uuid}/actions/disable')
        return self._parse_user(response['response'])
    
    async def reset_user_traffic(self, uuid: str) -> RemnaWaveUser:
        response = await self._make_request('POST', f'/api/users/{uuid}/actions/reset-traffic')
        return self._parse_user(response['response'])
    
    async def revoke_user_subscription(self, uuid: str, new_short_uuid: Optional[str] = None) -> RemnaWaveUser:
        data = {}
        if new_short_uuid:
            data['shortUuid'] = new_short_uuid
            
        response = await self._make_request('POST', f'/api/users/{uuid}/actions/revoke', data)
        return self._parse_user(response['response'])
    
    async def get_all_users(self, start: int = 0, size: int = 100) -> Dict[str, Any]:
        params = {'start': start, 'size': size}
        response = await self._make_request('GET', '/api/users', params=params)
        
        return {
            'users': [self._parse_user(user) for user in response['response']['users']],
            'total': response['response']['total']
        }
    
    
    async def get_internal_squads(self) -> List[RemnaWaveInternalSquad]:
        response = await self._make_request('GET', '/api/internal-squads')
        return [self._parse_internal_squad(squad) for squad in response['response']['internalSquads']]
    
    async def get_internal_squad_by_uuid(self, uuid: str) -> Optional[RemnaWaveInternalSquad]:
        try:
            response = await self._make_request('GET', f'/api/internal-squads/{uuid}')
            return self._parse_internal_squad(response['response'])
        except RemnaWaveAPIError as e:
            if e.status_code == 404:
                return None
            raise
    
    async def create_internal_squad(self, name: str, inbounds: List[str]) -> RemnaWaveInternalSquad:
        data = {
            'name': name,
            'inbounds': inbounds
        }
        response = await self._make_request('POST', '/api/internal-squads', data)
        return self._parse_internal_squad(response['response'])
    
    async def update_internal_squad(
        self, 
        uuid: str, 
        name: Optional[str] = None, 
        inbounds: Optional[List[str]] = None
    ) -> RemnaWaveInternalSquad:
        data = {'uuid': uuid}
        if name:
            data['name'] = name
        if inbounds is not None:
            data['inbounds'] = inbounds
            
        response = await self._make_request('PATCH', '/api/internal-squads', data)
        return self._parse_internal_squad(response['response'])
    
    async def delete_internal_squad(self, uuid: str) -> bool:
        response = await self._make_request('DELETE', f'/api/internal-squads/{uuid}')
        return response['response']['isDeleted']
    
    
    async def get_all_nodes(self) -> List[RemnaWaveNode]:
        response = await self._make_request('GET', '/api/nodes')
        return [self._parse_node(node) for node in response['response']]
    
    async def get_node_by_uuid(self, uuid: str) -> Optional[RemnaWaveNode]:
        try:
            response = await self._make_request('GET', f'/api/nodes/{uuid}')
            return self._parse_node(response['response'])
        except RemnaWaveAPIError as e:
            if e.status_code == 404:
                return None
            raise
    
    async def enable_node(self, uuid: str) -> RemnaWaveNode:
        response = await self._make_request('POST', f'/api/nodes/{uuid}/actions/enable')
        return self._parse_node(response['response'])
    
    async def disable_node(self, uuid: str) -> RemnaWaveNode:
        response = await self._make_request('POST', f'/api/nodes/{uuid}/actions/disable')
        return self._parse_node(response['response'])
    
    async def restart_node(self, uuid: str) -> bool:
        response = await self._make_request('POST', f'/api/nodes/{uuid}/actions/restart')
        return response['response']['eventSent']
    
    async def restart_all_nodes(self) -> bool:
        response = await self._make_request('POST', '/api/nodes/actions/restart-all')
        return response['response']['eventSent']
    
    
    async def get_subscription_info(self, short_uuid: str) -> SubscriptionInfo:
        response = await self._make_request('GET', f'/api/sub/{short_uuid}/info')
        return self._parse_subscription_info(response['response'])
    
    async def get_subscription_by_short_uuid(self, short_uuid: str) -> str:
        async with self.session.get(f"{self.base_url}/api/sub/{short_uuid}") as response:
            if response.status >= 400:
                raise RemnaWaveAPIError(f"Failed to get subscription: {response.status}")
            return await response.text()
    
    async def get_subscription_by_client_type(self, short_uuid: str, client_type: str) -> str:
        valid_types = ["stash", "singbox", "singbox-legacy", "mihomo", "json", "v2ray-json", "clash"]
        if client_type not in valid_types:
            raise ValueError(f"Invalid client type. Must be one of: {valid_types}")
        
        async with self.session.get(f"{self.base_url}/api/sub/{short_uuid}/{client_type}") as response:
            if response.status >= 400:
                raise RemnaWaveAPIError(f"Failed to get subscription: {response.status}")
            return await response.text()
    
    async def get_subscription_links(self, short_uuid: str) -> Dict[str, str]:
        base_url = f"{self.base_url}/api/sub/{short_uuid}"
        
        links = {
            "base": base_url,
            "stash": f"{base_url}/stash",
            "singbox": f"{base_url}/singbox", 
            "singbox_legacy": f"{base_url}/singbox-legacy",
            "mihomo": f"{base_url}/mihomo",
            "json": f"{base_url}/json",
            "v2ray_json": f"{base_url}/v2ray-json",
            "clash": f"{base_url}/clash"
        }
        
        return links
    
    async def get_outline_subscription(self, short_uuid: str, encoded_tag: str) -> str:
        async with self.session.get(f"{self.base_url}/api/sub/outline/{short_uuid}/ss/{encoded_tag}") as response:
            if response.status >= 400:
                raise RemnaWaveAPIError(f"Failed to get outline subscription: {response.status}")
            return await response.text()
    
    
    async def get_system_stats(self) -> Dict[str, Any]:
        response = await self._make_request('GET', '/api/system/stats')
        return response['response']
    
    async def get_bandwidth_stats(self) -> Dict[str, Any]:
        response = await self._make_request('GET', '/api/system/stats/bandwidth')
        return response['response']
    
    async def get_nodes_statistics(self) -> Dict[str, Any]:
        response = await self._make_request('GET', '/api/system/stats/nodes')
        return response['response']
    
    async def get_nodes_realtime_usage(self) -> List[Dict[str, Any]]:
        response = await self._make_request('GET', '/api/nodes/usage/realtime')
        return response['response']
    
    
    async def get_user_devices(self, user_uuid: str) -> Dict[str, Any]:
        try:
            response = await self._make_request('GET', f'/api/hwid/devices/{user_uuid}')
            return response['response']
        except RemnaWaveAPIError as e:
            if e.status_code == 404:
                return {'total': 0, 'devices': []}
            raise

    async def reset_user_devices(self, user_uuid: str) -> bool:
        try:
            devices_info = await self.get_user_devices(user_uuid)
            devices = devices_info.get('devices', [])
            
            if not devices:
                return True
            
            failed_count = 0
            for device in devices:
                device_hwid = device.get('hwid')
                if device_hwid:
                    try:
                        delete_data = {
                            "userUuid": user_uuid,
                            "hwid": device_hwid
                        }
                        await self._make_request('POST', '/api/hwid/devices/delete', data=delete_data)
                    except Exception as device_error:
                        logger.error(f"Ошибка удаления устройства {device_hwid}: {device_error}")
                        failed_count += 1
            
            return failed_count < len(devices) / 2
            
        except Exception as e:
            logger.error(f"Ошибка при сбросе устройств: {e}")
            return False

    async def remove_device(self, user_uuid: str, device_hwid: str) -> bool:
        try:
            delete_data = {
                "userUuid": user_uuid,
                "hwid": device_hwid
            }
            await self._make_request('POST', '/api/hwid/devices/delete', data=delete_data)
            return True
        except Exception as e:
            logger.error(f"Ошибка удаления устройства {device_hwid}: {e}")
            return False
    
    
    def _parse_user(self, user_data: Dict) -> RemnaWaveUser:
        return RemnaWaveUser(
            uuid=user_data['uuid'],
            short_uuid=user_data['shortUuid'],
            username=user_data['username'],
            status=UserStatus(user_data['status']),
            used_traffic_bytes=int(user_data['usedTrafficBytes']),
            lifetime_used_traffic_bytes=int(user_data['lifetimeUsedTrafficBytes']),
            traffic_limit_bytes=user_data['trafficLimitBytes'],
            traffic_limit_strategy=TrafficLimitStrategy(user_data['trafficLimitStrategy']),
            expire_at=datetime.fromisoformat(user_data['expireAt'].replace('Z', '+00:00')),
            telegram_id=user_data.get('telegramId'),
            email=user_data.get('email'),
            hwid_device_limit=user_data.get('hwidDeviceLimit'),
            description=user_data.get('description'),
            tag=user_data.get('tag'),
            subscription_url=normalize_subscription_url(user_data['subscriptionUrl']),
            active_internal_squads=user_data['activeInternalSquads'],
            created_at=datetime.fromisoformat(user_data['createdAt'].replace('Z', '+00:00')),
            updated_at=datetime.fromisoformat(user_data['updatedAt'].replace('Z', '+00:00')),
            sub_last_user_agent=user_data.get('subLastUserAgent'),
            sub_last_opened_at=self._parse_optional_datetime(user_data.get('subLastOpenedAt')),
            online_at=self._parse_optional_datetime(user_data.get('onlineAt')),
            sub_revoked_at=self._parse_optional_datetime(user_data.get('subRevokedAt')),
            last_traffic_reset_at=self._parse_optional_datetime(user_data.get('lastTrafficResetAt')),
            trojan_password=user_data.get('trojanPassword'),
            vless_uuid=user_data.get('vlessUuid'),
            ss_password=user_data.get('ssPassword'),
            first_connected_at=self._parse_optional_datetime(user_data.get('firstConnectedAt')),
            last_triggered_threshold=user_data.get('lastTriggeredThreshold', 0)
        )

    def _parse_optional_datetime(self, date_str: Optional[str]) -> Optional[datetime]:
        if date_str:
            return datetime.fromisoformat(date_str.replace('Z', '+00:00'))
        return None
    
    def _parse_internal_squad(self, squad_data: Dict) -> RemnaWaveInternalSquad:
        return RemnaWaveInternalSquad(
            uuid=squad_data['uuid'],
            name=squad_data['name'],
            members_count=squad_data['info']['membersCount'],
            inbounds_count=squad_data['info']['inboundsCount'],
            inbounds=squad_data['inbounds']
        )
    
    def _parse_node(self, node_data: Dict) -> RemnaWaveNode:
        return RemnaWaveNode(
            uuid=node_data['uuid'],
            name=node_data['name'],
            address=node_data['address'],
            country_code=node_data['countryCode'],
            is_connected=node_data['isConnected'],
            is_disabled=node_data['isDisabled'],
            is_node_online=node_data['isNodeOnline'],
            is_xray_running=node_data['isXrayRunning'],
            users_online=node_data.get('usersOnline'),
            traffic_used_bytes=node_data.get('trafficUsedBytes'),
            traffic_limit_bytes=node_data.get('trafficLimitBytes')
        )
    
    def _parse_subscription_info(self, data: Dict) -> SubscriptionInfo:
        return SubscriptionInfo(
            is_found=data['isFound'],
            user=data.get('user'),
            links=data.get('links', []),
            ss_conf_links=data.get('ssConfLinks', {}),
            subscription_url=normalize_subscription_url(data.get('subscriptionUrl', '')),
            happ=data.get('happ')
        )


def format_bytes(bytes_value: int) -> str:
    if bytes_value == 0:
        return "0 B"
    
    units = ["B", "KB", "MB", "GB", "TB"]
    size = bytes_value
    unit_index = 0
    
    while size >= 1024 and unit_index < len(units) - 1:
        size /= 1024
        unit_index += 1
    
    return f"{size:.1f} {units[unit_index]}"


def parse_bytes(size_str: str) -> int:
    size_str = size_str.upper().strip()
    
    units = {
        'B': 1,
        'KB': 1024,
        'MB': 1024 ** 2,
        'GB': 1024 ** 3,
        'TB': 1024 ** 4
    }
    
    for unit, multiplier in units.items():
        if size_str.endswith(unit):
            try:
                value = float(size_str[:-len(unit)].strip())
                return int(value * multiplier)
            except ValueError:
                break
    
    return 0


async def test_api_connection(api: RemnaWaveAPI) -> bool:
    try:
        await api.get_system_stats()
        return True
    except Exception as e:
        logger.error(f"API connection test failed: {e}")
        return False
