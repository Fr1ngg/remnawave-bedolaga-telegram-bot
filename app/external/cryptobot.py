import logging
import hashlib
import hmac
import json
import aiohttp
from typing import Optional, Dict, Any
from datetime import datetime

from app.config import settings

logger = logging.getLogger(__name__)


class CryptoBotService:
    
    def __init__(self):
        self.api_token = settings.CRYPTOBOT_API_TOKEN
        self.base_url = settings.get_cryptobot_base_url()
        self.webhook_secret = settings.CRYPTOBOT_WEBHOOK_SECRET
    
    async def _make_request(
        self,
        method: str,
        endpoint: str,
        data: Optional[Dict] = None,
    ) -> Optional[Dict[str, Any]]:
        
        if not self.api_token:
            logger.error("CryptoBot API token не настроен")
            return None
        
        url = f"{self.base_url}/api/{endpoint}"
        headers = {
            'Crypto-Pay-API-Token': self.api_token,
            'Content-Type': 'application/json'
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                request_kwargs: Dict[str, Any] = {"headers": headers}

                if method.upper() == "GET":
                    if data:
                        request_kwargs["params"] = data
                elif data:
                    request_kwargs["json"] = data

                async with session.request(
                    method,
                    url,
                    **request_kwargs,
                ) as response:
                    
                    response_data = await response.json()
                    
                    if response.status == 200 and response_data.get('ok'):
                        return response_data.get('result')
                    else:
                        logger.error(f"CryptoBot API ошибка: {response_data}")
                        return None
                        
        except Exception as e:
            logger.error(f"Ошибка запроса к CryptoBot API: {e}")
            return None
    
    async def get_me(self) -> Optional[Dict[str, Any]]:
        return await self._make_request('GET', 'getMe')
    
    async def create_invoice(
        self,
        amount: str,
        asset: str = "USDT",
        description: Optional[str] = None,
        payload: Optional[str] = None,
        expires_in: Optional[int] = None
    ) -> Optional[Dict[str, Any]]:
        
        data = {
            'currency_type': 'crypto',
            'asset': asset,
            'amount': amount
        }
        
        if description:
            data['description'] = description
        
        if payload:
            data['payload'] = payload
        
        if expires_in:
            data['expires_in'] = expires_in
        
        result = await self._make_request('POST', 'createInvoice', data)
        
        if result:
            logger.info(f"Создан CryptoBot invoice {result.get('invoice_id')} на {amount} {asset}")
        
        return result
    
    async def get_invoices(
        self,
        asset: Optional[str] = None,
        status: Optional[str] = None,
        offset: int = 0,
        count: int = 100,
        invoice_ids: Optional[list] = None,
    ) -> Optional[list]:

        data = {
            'offset': offset,
            'count': count
        }

        if asset:
            data['asset'] = asset

        if status:
            data['status'] = status

        if invoice_ids:
            data['invoice_ids'] = invoice_ids

        result = await self._make_request('GET', 'getInvoices', data)

        if isinstance(result, dict):
            items = result.get('items')
            return items if isinstance(items, list) else []

        if isinstance(result, list):
            return result

        return []
    
    async def get_balance(self) -> Optional[list]:
        return await self._make_request('GET', 'getBalance')
    
    async def get_exchange_rates(self) -> Optional[list]:
        return await self._make_request('GET', 'getExchangeRates')
    
    def verify_webhook_signature(self, body: str, signature: str) -> bool:
        
        if not self.webhook_secret:
            logger.warning("CryptoBot webhook secret не настроен")
            return True
        
        try:
            secret_hash = hashlib.sha256(self.webhook_secret.encode()).digest()
            expected_signature = hmac.new(secret_hash, body.encode(), hashlib.sha256).hexdigest()
            
            is_valid = hmac.compare_digest(signature, expected_signature)
            
            if is_valid:
                logger.info("✅ CryptoBot webhook подпись валидна")
            else:
                logger.error("❌ Неверная подпись CryptoBot webhook")
            
            return is_valid
            
        except Exception as e:
            logger.error(f"Ошибка проверки подписи CryptoBot webhook: {e}")
            return False
    
    async def process_webhook(self, webhook_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        
        try:
            update_type = webhook_data.get('update_type')
            
            if update_type == 'invoice_paid':
                invoice_data = webhook_data.get('payload', {})
                
                return {
                    'event_type': 'payment',
                    'payment_id': str(invoice_data.get('invoice_id')),
                    'amount': invoice_data.get('amount'),
                    'asset': invoice_data.get('asset'),
                    'status': 'paid',
                    'user_payload': invoice_data.get('payload'),
                    'paid_at': invoice_data.get('paid_at'),
                    'payment_system': 'cryptobot'
                }
            
            logger.warning(f"Неизвестный тип CryptoBot webhook: {update_type}")
            return None
            
        except Exception as e:
            logger.error(f"Ошибка обработки CryptoBot webhook: {e}")
            return None
