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

# IDs de usuários que podem usar comandos de ADMIN (além dos administradores do servidor)
# Exemplo: ADMIN_USER_IDS=123456789,987654321
# Deixe vazio para permitir apenas administradores do servidor
ADMIN_USER_IDS = os.getenv('ADMIN_USER_IDS', '').split(',') if os.getenv('ADMIN_USER_IDS') else []
ADMIN_USER_IDS = [user_id.strip() for user_id in ADMIN_USER_IDS if user_id.strip()]

# IDs dos cargos que podem usar comandos de ADMIN (além dos administradores do servidor)
# Exemplo: ADMIN_ROLE_IDS=123456789,987654321
# Deixe vazio para permitir apenas administradores do servidor
ADMIN_ROLE_IDS = os.getenv('ADMIN_ROLE_IDS', '').split(',') if os.getenv('ADMIN_ROLE_IDS') else []
ADMIN_ROLE_IDS = [role_id.strip() for role_id in ADMIN_ROLE_IDS if role_id.strip()]

# ID do canal para notificações de registro/atualização
NOTIFICATION_CHANNEL_ID = 1442347196103004242

# ID do canal para relatórios de DM em massa
DM_REPORT_CHANNEL_ID = 1442359729044066405

# ID do canal para listas de membros em salas de voz
LIST_CHANNEL_ID = 1412698827026075779

# ID do canal para logs de movimentação de membros entre salas
MOVE_LOG_CHANNEL_ID = 1442371565022089237

# ID do cargo que indica que o player faz parte da guilda (MOUZ/MANIFEST)
GUILD_MEMBER_ROLE_ID = 1412255708967207012

# ID do cargo "Registrado" - dado quando o player faz registro de gearscore
REGISTERED_ROLE_ID = 1442888990997876817

# ID do cargo "Não Registrado" - dado a membros da guilda que ainda não fizeram registro
UNREGISTERED_ROLE_ID = 1442889439717359728

# ID do cargo "Amigo" - dado a quem tinha o cargo da guilda mas não está mais na guilda no jogo
FRIEND_ROLE_ID = 1412707507171889172

# Guildas do jogo (perfil público no site oficial) usadas para verificar quem ainda está na guilda
BDO_GUILD_NAMES = ["MOUZ", "Manifest"]
BDO_GUILD_REGION = "SA"

# ID do canal com o painel e os cards de aprovação de quem saiu da guilda
GUILD_CHECK_CHANNEL_ID = 1556745244995883030

# Configurações de lembrete automático de atualização de GS
GS_UPDATE_REMINDER_DAYS = 10  # Dias sem atualizar para enviar lembrete
GS_REMINDER_CHECK_HOUR = 12  # Hora do dia para verificar (12 = meio-dia)


# Lista de classes do Black Desert Online (ordem alfabética)
# Total: 31 classes - Discord mostra 25, usuário deve DIGITAR para filtrar
BDO_CLASSES = [
    "Arqueiro",
    "Berserker",
    "Bruxa",
    "Corsair",
    "Dark Knight",
    "Deadeye",
    "Dosa",
    "Drakania",
    "Feiticeira",
    "Guardian",
    "Hashashin",
    "Kunoichi",
    "Lahn",
    "Maegu",
    "Maehwa",
    "Mago",
    "Mística",
    "Musa",
    "Ninja",
    "Nova",
    "Ranger",
    "Sage",
    "Savior",
    "Scholar",
    "Shai",
    "Striker",
    "Tamer",
    "Valkyrie",
    "Warrior",
    "Woosa",
    "Wukong"
]

