import os
from dotenv import load_dotenv

load_dotenv()

# Token do bot Discord
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')

# Configurações do banco de dados
# Para SQLite (local):
DATABASE_NAME = 'bdo_gearscore.db'

# Para PostgreSQL (Supabase, Railway, Neon, etc.):
DATABASE_URL = os.getenv('DATABASE_URL')

# Para MongoDB Atlas:
MONGODB_URI = os.getenv('MONGODB_URI')
MONGODB_DB_NAME = os.getenv('MONGODB_DB_NAME', 'bdo_gearscore')

# Configurações de permissões para comandos de DM em massa
# IDs dos cargos que podem usar comandos de DM em massa (separados por vírgula)
# Exemplo: ALLOWED_DM_ROLES=123456789,987654321
# Deixe vazio ou None para permitir apenas administradores
ALLOWED_DM_ROLES = os.getenv('ALLOWED_DM_ROLES', '').split(',') if os.getenv('ALLOWED_DM_ROLES') else []
ALLOWED_DM_ROLES = [role_id.strip() for role_id in ALLOWED_DM_ROLES if role_id.strip()]

# ID do canal para notificações de registro/atualização
NOTIFICATION_CHANNEL_ID = 1442347196103004242

# ID do cargo que indica que o player faz parte da guilda (MOUZ/MANIFEST)
GUILD_MEMBER_ROLE_ID = 1412255708967207012

# Lista de classes do Black Desert Online
BDO_CLASSES = [
    "Warrior",
    "Ranger",
    "Feiticeira",
    "Berserker",
    "Valkyrie",
    "Mago",
    "Bruxa",
    "Tamer",
    "Musa",
    "Maehwa",
    "Ninja",
    "Kunoichi",
    "Mística",
    "Striker",
    "Lahn",
    "Arqueiro",
    "Shai",
    "Guardian",
    "Hashashin",
    "Nova",
    "Sage",
    "Corsair",
    "Drakania",
    "Woosa",
    "Maegu",
    "Scholar",
    "Dosa",
    "Deadeye",
    "Wukong"
]

