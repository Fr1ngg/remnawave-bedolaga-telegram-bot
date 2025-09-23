from aiogram.fsm.state import State, StatesGroup

class RegistrationStates(StatesGroup):
    waiting_for_rules_accept = State()
    waiting_for_referral_code = State()

class SubscriptionStates(StatesGroup):
    selecting_period = State()
    selecting_traffic = State()
    selecting_countries = State()
    selecting_devices = State()
    confirming_purchase = State()
    
    adding_countries = State()
    adding_traffic = State()
    adding_devices = State()
    extending_subscription = State()
    confirming_traffic_reset = State()
    cart_saved_for_topup = State()

class BalanceStates(StatesGroup):
    waiting_for_amount = State()
    waiting_for_stars_payment = State()
    waiting_for_support_request = State()


class PromoCodeStates(StatesGroup):
    waiting_for_code = State()
    waiting_for_referral_code = State()

class AdminStates(StatesGroup):
    
    waiting_for_user_search = State()
    editing_user_balance = State()
    extending_subscription = State()
    adding_traffic = State()
    granting_subscription = State()
    editing_user_subscription = State()
    
    creating_promocode = State()
    setting_promocode_type = State()
    setting_promocode_value = State()
    setting_promocode_uses = State()
    setting_promocode_expiry = State()
    setting_promocode_trial_devices = State()
    setting_promocode_trial_traffic = State()
    setting_promocode_trial_reset = State()
    selecting_promocode_trial_squads = State()

    creating_campaign_name = State()
    creating_campaign_start = State()
    creating_campaign_bonus = State()
    creating_campaign_balance = State()
    creating_campaign_subscription_days = State()
    creating_campaign_subscription_traffic = State()
    creating_campaign_subscription_devices = State()
    creating_campaign_subscription_servers = State()

    editing_campaign_name = State()
    editing_campaign_start = State()
    editing_campaign_balance = State()
    editing_campaign_subscription_days = State()
    editing_campaign_subscription_traffic = State()
    editing_campaign_subscription_devices = State()
    editing_campaign_subscription_servers = State()

    waiting_for_broadcast_message = State()
    waiting_for_broadcast_media = State()
    confirming_broadcast = State()

    creating_promo_group_name = State()
    creating_promo_group_traffic_discount = State()
    creating_promo_group_server_discount = State()
    creating_promo_group_device_discount = State()

    editing_promo_group_name = State()
    editing_promo_group_traffic_discount = State()
    editing_promo_group_server_discount = State()
    editing_promo_group_device_discount = State()
    
    editing_squad_price = State()
    editing_traffic_price = State()
    editing_device_price = State()
    editing_user_devices = State()
    editing_user_traffic = State()
    
    editing_rules_page = State()
    
    confirming_sync = State()

    editing_server_name = State()
    editing_server_price = State()
    editing_server_country = State()
    editing_server_limit = State()
    editing_server_description = State()
    
    creating_server_uuid = State()
    creating_server_name = State()
    creating_server_price = State()
    creating_server_country = State()
    
    editing_welcome_text = State()
    waiting_for_message_buttons = "waiting_for_message_buttons"
    
    # Состояния для отслеживания источника перехода
    viewing_user_from_balance_list = State()

class SupportStates(StatesGroup):
    waiting_for_message = State()

class TicketStates(StatesGroup):
    waiting_for_title = State()
    waiting_for_message = State()
    waiting_for_reply = State()

class AdminTicketStates(StatesGroup):
    waiting_for_reply = State()
    waiting_for_block_duration = State()

class SupportSettingsStates(StatesGroup):
    waiting_for_desc = State()

class AutoPayStates(StatesGroup):
    setting_autopay_days = State()
    confirming_autopay_toggle = State()

class SquadCreateStates(StatesGroup):
    waiting_for_name = State()
    selecting_inbounds = State()

class SquadRenameStates(StatesGroup):
    waiting_for_new_name = State()

class AdminSubmenuStates(StatesGroup):
    in_users_submenu = State()
    in_promo_submenu = State()
    in_communications_submenu = State()
    in_settings_submenu = State()
    in_system_submenu = State()
