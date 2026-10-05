import discord
from discord import app_commands
from discord.ext import commands, tasks
import os
import io
import asyncio
import re
import html
import aiohttp
import logging
from datetime import datetime
from pytz import timezone
from config import DISCORD_TOKEN, BDO_CLASSES, DATABASE_NAME, DATABASE_URL, ALLOWED_DM_ROLES, NOTIFICATION_CHANNEL_ID, GUILD_MEMBER_ROLE_ID, DM_REPORT_CHANNEL_ID, LIST_CHANNEL_ID, MOVE_LOG_CHANNEL_ID, REGISTERED_ROLE_ID, UNREGISTERED_ROLE_ID, GS_UPDATE_REMINDER_DAYS, GS_REMINDER_CHECK_HOUR, ADMIN_USER_IDS, ADMIN_ROLE_IDS, FRIEND_ROLE_ID, BDO_GUILD_NAMES, BDO_GUILD_REGION, GUILD_CHECK_CHANNEL_ID
from datetime import timedelta
import hp_efetivo
# Importar o banco de dados apropriado
if DATABASE_URL:
    from database_postgres import Database
else:
    from database import Database

# Configuração do bot
intents = discord.Intents.default()
intents.message_content = True
intents.members = True  # Necessário para ver membros e cargos
intents.presences = True  # Necessário para ver status online/offline
bot = commands.Bot(command_prefix='!', intents=intents)


# Função helper para verificar se usuário tem permissão de admin
def is_admin_user(user) -> bool:
    """
    Verifica se o usuário pode usar comandos de admin.
    Retorna True se:
    - O usuário é administrador do servidor OU
    - O ID do usuário está na lista ADMIN_USER_IDS OU
    - O usuário possui algum cargo da lista ADMIN_ROLE_IDS
    
    Aceita tanto discord.Member quanto discord.User (tenta obter Member do guild se necessário)
    """
    # Se não for Member, tentar obter do guild
    if not isinstance(user, discord.Member):
        # Se não houver guild, não pode verificar permissões
        if not hasattr(user, 'guild') or not user.guild:
            user_id_str = str(user.id)
            # Verificar apenas por ID de usuário
            if ADMIN_USER_IDS and user_id_str in ADMIN_USER_IDS:
                logger.info(f"[ADMIN] ✅ Usuário {user.display_name} (ID: {user_id_str}) autorizado via ADMIN_USER_IDS (sem guild)")
                return True
            logger.info(f"[ADMIN] ❌ Usuário {user.display_name} (ID: {user_id_str}) NÃO autorizado (sem guild)")
            return False
    
    user_id_str = str(user.id)
    display_name = getattr(user, 'display_name', getattr(user, 'name', 'Unknown'))
    
    # Log detalhado para debug
    logger.info(f"[ADMIN] Verificando acesso para {display_name} (ID: {user_id_str})")
    
    # Verificar se é administrador do servidor
    if hasattr(user, 'guild_permissions') and user.guild_permissions.administrator:
        logger.info(f"[ADMIN] ✅ Usuário {display_name} (ID: {user_id_str}) autorizado como administrador do servidor")
        return True
    
    # Verificar se está na lista de usuários admin
    if ADMIN_USER_IDS and user_id_str in ADMIN_USER_IDS:
        logger.info(f"[ADMIN] ✅ Usuário {display_name} (ID: {user_id_str}) autorizado via ADMIN_USER_IDS")
        return True
    
    # Verificar se possui algum cargo admin
    if ADMIN_ROLE_IDS and hasattr(user, 'roles'):
        member_role_ids = [str(role.id) for role in user.roles]
        logger.info(f"[ADMIN] Cargos do usuário {display_name}: {member_role_ids}")
        logger.info(f"[ADMIN] ADMIN_ROLE_IDS configurado: {ADMIN_ROLE_IDS}")
        
        matching_roles = [role for role in user.roles if str(role.id) in ADMIN_ROLE_IDS]
        if matching_roles:
            logger.info(f"[ADMIN] ✅ Usuário {display_name} (ID: {user_id_str}) autorizado via ADMIN_ROLE_IDS. Cargos correspondentes: {[role.name for role in matching_roles]}")
            return True
        else:
            logger.info(f"[ADMIN] ❌ Usuário {display_name} (ID: {user_id_str}) NÃO possui nenhum cargo da lista ADMIN_ROLE_IDS")
    elif ADMIN_ROLE_IDS:
        logger.info(f"[ADMIN] ADMIN_ROLE_IDS configurado mas usuário não tem atributo 'roles'")
    else:
        logger.info(f"[ADMIN] ADMIN_ROLE_IDS não configurado ou vazio")
    
    logger.info(f"[ADMIN] ❌ Usuário {display_name} (ID: {user_id_str}) NÃO autorizado")
    return False

# Função helper para verificar se usuário tem permissão para usar comandos de DM em massa
def has_dm_permission(member: discord.Member) -> bool:
    """Verifica se o membro tem permissão para usar comandos de DM em massa"""
    # Apenas membros com cargos específicos podem usar (mesmo sendo admin)
    if ALLOWED_DM_ROLES:
        member_role_ids = [str(role.id) for role in member.roles]
        return any(role_id in member_role_ids for role_id in ALLOWED_DM_ROLES)
    
    # Se não há cargos configurados, ninguém pode usar (exceto se for admin e não houver lista)
    # Por padrão, se não houver lista, apenas administradores podem usar
    return member.guild_permissions.administrator

# Configurar sistema de logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# Inicializar banco de dados
db = Database()
logger.info("Banco de dados inicializado")

# Função helper para calcular GS corretamente (MAX(AP, AAP) + DP)
def calculate_gs(ap, aap, dp):
    """Calcula o Gearscore: maior entre AP ou AAP + DP"""
    return max(ap, aap) + dp

# Função helper para calcular posição no ranking (otimizada)
async def get_player_ranking_position(guild: discord.Guild, user_id: str, current_gs: int):
    """
    Calcula a posição do player no ranking de GS da guilda.
    Retorna: (posicao, total_players, players_acima, players_abaixo, percentil)
    """
    try:
        # Buscar apenas membros que têm o cargo da guilda
        valid_user_ids = await get_guild_member_ids(guild)
        logger.debug(f"get_player_ranking_position: valid_user_ids count = {len(valid_user_ids)}")
        
        if not valid_user_ids:
            logger.debug("get_player_ranking_position: Nenhum membro válido encontrado")
            return None
        
        # Se o user_id não está na lista de válidos, não tem ranking
        # Garantir que user_id é string para comparação
        user_id_str = str(user_id)
        if user_id_str not in valid_user_ids:
            logger.debug(f"get_player_ranking_position: user_id {user_id_str} não está na lista de válidos (total: {len(valid_user_ids)})")
            return None
        
        # Buscar todos os gearscores (já filtrados por valid_user_ids)
        all_gearscores = db.get_all_gearscores(valid_user_ids=valid_user_ids)
        logger.debug(f"get_player_ranking_position: all_gearscores count = {len(all_gearscores) if all_gearscores else 0}")
        
        if not all_gearscores:
            logger.debug("get_player_ranking_position: Nenhum gearscore encontrado")
            return None
        
        # Processar e calcular GS de cada player (otimizado - sem verificar cargo novamente)
        players_gs = []
        for record in all_gearscores:
            # Extrair dados do registro
            if isinstance(record, dict):
                record_user_id = record.get('user_id', '')
                ap = record.get('ap', 0)
                aap = record.get('aap', 0)
                dp = record.get('dp', 0)
            else:
                record_user_id = record[1] if len(record) > 1 else ''
                ap = record[5] if len(record) > 5 else 0
                aap = record[6] if len(record) > 6 else 0
                dp = record[7] if len(record) > 7 else 0
            
            # Não precisa verificar cargo novamente - já foi filtrado por valid_user_ids
            # Apenas garantir que o user_id está no set válido (comparar como strings)
            if str(record_user_id) not in valid_user_ids:
                continue
            
            gs = max(ap, aap) + dp
            players_gs.append({
                'user_id': record_user_id,
                'gs': gs
            })
        
        if not players_gs:
            return None
        
        # Ordenar por GS (maior para menor)
        players_gs.sort(key=lambda x: x['gs'], reverse=True)
        
        # Encontrar posição do player atual
        posicao = None
        for idx, player in enumerate(players_gs, 1):
            # Comparar como strings para garantir compatibilidade
            if str(player['user_id']) == user_id_str:
                posicao = idx
                break
        
        if posicao is None:
            logger.debug(f"get_player_ranking_position: Posição não encontrada para user_id {user_id}")
            return None
        
        total_players = len(players_gs)
        players_acima = posicao - 1
        players_abaixo = total_players - posicao
        
        # Calcular percentil (0-100, onde 100 é o melhor)
        percentil = int(round((total_players - posicao + 1) / total_players * 100))
        
        result = {
            'posicao': posicao,
            'total_players': total_players,
            'players_acima': players_acima,
            'players_abaixo': players_abaixo,
            'percentil': percentil
        }
        logger.debug(f"get_player_ranking_position: Resultado = {result}")
        return result
    except Exception as e:
        logger.error(f"Erro ao calcular ranking: {e}", exc_info=True)
        return None

# Função helper para verificar se um membro tem o cargo da guilda
def has_guild_role(member: discord.Member) -> bool:
    """Verifica se o membro tem o cargo que indica participação na guilda"""
    if not member or not member.guild:
        return False
    return any(role.id == GUILD_MEMBER_ROLE_ID for role in member.roles)

# Função helper para obter todos os user_ids que têm o cargo da guilda
async def get_guild_member_ids(guild: discord.Guild) -> set:
    """Retorna um set com todos os IDs de usuários que têm o cargo da guilda"""
    member_ids = set()
    if not guild:
        return member_ids
    
    role = guild.get_role(GUILD_MEMBER_ROLE_ID)
    if not role:
        return member_ids
    
    for member in guild.members:
        if has_guild_role(member):
            member_ids.add(str(member.id))
    
    return member_ids

# Função helper para obter a guilda da interação (inclusive quando usada via DM)
async def resolve_interaction_guild(interaction: discord.Interaction):
    """
    Retorna a guilda da interação.
    Em DM, retorna a guilda do bot onde o usuário é membro e possui o cargo da guilda.
    Retorna: (guild, erro) - erro é None quando a guilda foi resolvida
    """
    if interaction.guild:
        return interaction.guild, None

    found_member = False
    for guild in interaction.client.guilds:
        member = guild.get_member(interaction.user.id)
        if member is None:
            try:
                member = await guild.fetch_member(interaction.user.id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                continue
        found_member = True
        if has_guild_role(member):
            return guild, None

    if found_member:
        return None, "❌ Você não possui o cargo da guilda no Discord. Só membros da guilda podem atualizar o gearscore."
    return None, "❌ Não encontrei você no servidor da guilda. Use este comando no servidor."

# Função helper para atualizar o nickname do membro para o nome de família
async def update_member_nickname(member: discord.Member, family_name: str) -> tuple:
    """
    Atualiza o nickname do membro para o nome de família.
    Retorna: (sucesso: bool, mensagem: str)
    """
    if not member or not member.guild:
        return False, "Membro não encontrado"
    
    # Não pode alterar nickname do dono do servidor
    if member.id == member.guild.owner_id:
        return False, "Não é possível alterar o nickname do dono do servidor"
    
    # Verificar se o bot tem permissão
    bot_member = member.guild.me
    if not bot_member.guild_permissions.manage_nicknames:
        return False, "Bot sem permissão para gerenciar nicknames"
    
    # Verificar hierarquia de cargos
    if member.top_role >= bot_member.top_role:
        return False, "Membro tem cargo igual ou superior ao bot"
    
    try:
        # Limitar nickname a 32 caracteres (limite do Discord)
        nickname = family_name[:32] if len(family_name) > 32 else family_name
        await member.edit(nick=nickname, reason="Atualização automática para nome de família")
        return True, f"Nickname atualizado para '{nickname}'"
    except discord.Forbidden:
        return False, "Sem permissão para alterar nickname deste membro"
    except discord.HTTPException as e:
        return False, f"Erro ao alterar nickname: {str(e)}"

# Função helper para gerenciar cargos de registro
async def update_registration_roles(member: discord.Member, has_registration: bool):
    """Atualiza os cargos de registro do membro baseado no status de registro"""
    if not member or not member.guild:
        return
    
    registered_role = member.guild.get_role(REGISTERED_ROLE_ID)
    unregistered_role = member.guild.get_role(UNREGISTERED_ROLE_ID)
    
    try:
        if has_registration:
            # Tem registro: dar cargo "Registrado" e remover "Não Registrado"
            if registered_role and registered_role not in member.roles:
                await member.add_roles(registered_role, reason="Registro de gearscore")
            if unregistered_role and unregistered_role in member.roles:
                await member.remove_roles(unregistered_role, reason="Registro de gearscore realizado")
        else:
            # Não tem registro: remover "Registrado" e dar "Não Registrado" (se tiver cargo membro)
            if registered_role and registered_role in member.roles:
                await member.remove_roles(registered_role, reason="Sem registro de gearscore")
            if unregistered_role and has_guild_role(member) and unregistered_role not in member.roles:
                await member.add_roles(unregistered_role, reason="Membro da guilda sem registro")
    except discord.Forbidden:
        logger.warning(f"Sem permissão para gerenciar cargos de {member.display_name} (ID: {member.id})")
    except discord.HTTPException as e:
        logger.error(f"Erro ao gerenciar cargos de {member.display_name} (ID: {member.id}): {e}")

# Função helper para verificar e atualizar cargos de todos os membros da guilda
async def sync_registration_roles(guild: discord.Guild):
    """Sincroniza os cargos de registro de todos os membros da guilda"""
    if not guild:
        return
    
    # Buscar todos os membros com cargo da guilda
    guild_member_ids = await get_guild_member_ids(guild)
    
    # Buscar todos os registros do banco
    all_registered = db.get_all_gearscores(valid_user_ids=guild_member_ids)
    registered_user_ids = set()
    
    for record in all_registered:
        if isinstance(record, dict):
            user_id = record.get('user_id', '')
        else:
            user_id = record[1] if len(record) > 1 else ''
        if user_id:
            registered_user_ids.add(str(user_id))
    
    # Atualizar cargos de cada membro
    for user_id in guild_member_ids:
        member = guild.get_member(int(user_id))
        if member:
            has_registration = user_id in registered_user_ids
            await update_registration_roles(member, has_registration)

# Função helper para verificar e enviar lembretes de atualização de GS
async def check_gs_update_reminders(guild: discord.Guild):
    """Verifica membros que não atualizaram GS nos últimos X dias e envia lembrete"""
    if not guild:
        return
    
    # Buscar todos os membros com cargo da guilda
    guild_member_ids = await get_guild_member_ids(guild)
    
    if not guild_member_ids:
        return
    
    # Buscar todos os registros do banco
    all_registered = db.get_all_gearscores(valid_user_ids=guild_member_ids)
    
    # Data limite para considerar desatualizado
    now = datetime.now()
    limit_date = now - timedelta(days=GS_UPDATE_REMINDER_DAYS)
    
    reminders_sent = 0
    errors = 0
    
    for record in all_registered:
        try:
            # Extrair dados do registro
            if isinstance(record, dict):
                user_id = record.get('user_id', '')
                family_name = record.get('family_name', 'N/A')
                class_pvp = record.get('class_pvp', 'N/A')
                ap = record.get('ap', 0)
                aap = record.get('aap', 0)
                dp = record.get('dp', 0)
                updated_at = record.get('updated_at')
            else:
                # Ordem das colunas: id(0), user_id(1), family_name(2), character_name(3), class_pvp(4), ap(5), aap(6), dp(7), linkgear(8), updated_at(9)
                user_id = str(record[1]) if len(record) > 1 else ''
                family_name = record[2] if len(record) > 2 else 'N/A'
                class_pvp = record[4] if len(record) > 4 else 'N/A'
                ap = record[5] if len(record) > 5 else 0
                aap = record[6] if len(record) > 6 else 0
                dp = record[7] if len(record) > 7 else 0
                updated_at = record[9] if len(record) > 9 else None
            
            if not user_id or not updated_at:
                continue
            
            # Converter updated_at para datetime
            if isinstance(updated_at, str):
                # Tentar diferentes formatos
                for fmt in ['%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M:%S.%f', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%dT%H:%M:%S.%f']:
                    try:
                        updated_datetime = datetime.strptime(updated_at.split('+')[0].split('Z')[0], fmt)
                        break
                    except:
                        continue
                else:
                    continue
            elif hasattr(updated_at, 'replace'):  # datetime object
                updated_datetime = updated_at.replace(tzinfo=None) if updated_at.tzinfo else updated_at
            else:
                continue
            
            # Verificar se está desatualizado
            if updated_datetime >= limit_date:
                continue  # Atualizado recentemente, pular
            
            # Calcular dias desde última atualização
            days_since_update = (now - updated_datetime).days
            
            # Buscar membro no servidor
            member = guild.get_member(int(user_id))
            if not member:
                continue
            
            # Verificar se ainda tem o cargo da guilda
            if not has_guild_role(member):
                continue
            
            # Calcular GS atual
            gs_total = calculate_gs(ap, aap, dp)
            
            # Criar embed de lembrete
            embed = discord.Embed(
                title="⏰ Lembrete de Atualização de Gearscore",
                description=(
                    f"Olá **{member.display_name}**!\n\n"
                    f"Seu gearscore não é atualizado há **{days_since_update} dias**.\n\n"
                    f"📋 **Por favor, atualize seu gearscore usando `/atualizar`**\n\n"
                    f"⚠️ **Importante:** Mesmo que você não tenha evoluído nada, "
                    f"por favor preencha novamente. Isso é necessário para o **controle interno da guilda**."
                ),
                color=discord.Color.orange(),
                timestamp=discord.utils.utcnow()
            )
            
            embed.add_field(name="👤 Família", value=family_name, inline=True)
            embed.add_field(name="⚔️ Classe", value=class_pvp, inline=True)
            embed.add_field(name="📊 GS Atual", value=f"**{gs_total}**", inline=True)
            embed.add_field(name="⚔️ AP", value=str(ap), inline=True)
            embed.add_field(name="🔥 AAP", value=str(aap), inline=True)
            embed.add_field(name="🛡️ DP", value=str(dp), inline=True)
            
            embed.add_field(
                name="📝 Como atualizar",
                value="Use o comando `/atualizar` com seus valores atuais de AP, AAP e DP (o link do gear é opcional).",
                inline=False
            )
            
            embed.set_footer(text=f"Última atualização: {updated_datetime.strftime('%d/%m/%Y às %H:%M')}")
            
            # Enviar DM
            try:
                await member.send(embed=embed)
                reminders_sent += 1
                logger.info(f"Lembrete de GS enviado para {member.display_name} (ID: {user_id}) - {days_since_update} dias sem atualizar")
            except discord.Forbidden:
                logger.warning(f"Não foi possível enviar lembrete para {member.display_name} (ID: {user_id}) - DM bloqueada")
            except Exception as e:
                logger.error(f"Erro ao enviar lembrete para {member.display_name} (ID: {user_id}): {e}")
                errors += 1
                
        except Exception as e:
            logger.error(f"Erro ao processar registro para lembrete: {e}")
            errors += 1
    
    return reminders_sent, errors

# Task que roda diariamente para enviar lembretes
@tasks.loop(hours=24)
async def gs_reminder_task():
    """Task que verifica e envia lembretes de atualização de GS diariamente"""
    logger.info("Iniciando verificação de lembretes de atualização de GS...")
    
    for guild in bot.guilds:
        try:
            reminders_sent, errors = await check_gs_update_reminders(guild)
            logger.info(f"Lembretes de GS para {guild.name}: {reminders_sent} enviados, {errors} erros")
        except Exception as e:
            logger.error(f"Erro ao processar lembretes para {guild.name}: {e}")
    
    logger.info("Verificação de lembretes de atualização de GS concluída")

@gs_reminder_task.before_loop
async def before_gs_reminder():
    """Aguarda o bot estar pronto antes de iniciar a task"""
    await bot.wait_until_ready()
    
    # Calcular tempo até a próxima execução no horário configurado
    now = datetime.now()
    target_time = now.replace(hour=GS_REMINDER_CHECK_HOUR, minute=0, second=0, microsecond=0)
    
    if now >= target_time:
        # Se já passou do horário hoje, agendar para amanhã
        target_time += timedelta(days=1)
    
    wait_seconds = (target_time - now).total_seconds()
    logger.info(f"Task de lembrete de GS agendada para {target_time.strftime('%d/%m/%Y às %H:%M')} ({int(wait_seconds/3600)}h {int((wait_seconds%3600)/60)}min)")
    
    await discord.utils.sleep_until(target_time)

# Task para limpar eventos do mês anterior (roda no dia 1 de cada mês)
@tasks.loop(hours=24)
async def eventos_reset_task():
    """Task que limpa eventos do mês anterior no dia 1 de cada mês"""
    now = datetime.now()
    
    # Só executa no dia 1
    if now.day == 1:
        logger.info("Dia 1 do mês - Iniciando reset de eventos do mês anterior...")
        try:
            deleted = db.limpar_eventos_mes_anterior()
            logger.info(f"Reset de eventos concluído: {deleted} eventos removidos")
        except Exception as e:
            logger.error(f"Erro ao fazer reset de eventos: {e}")

@eventos_reset_task.before_loop
async def before_eventos_reset():
    """Aguarda o bot estar pronto antes de iniciar a task"""
    await bot.wait_until_ready()
    logger.info("Task de reset mensal de eventos iniciada")

# Função helper para enviar notificação ao canal
async def send_notification_to_channel(bot, interaction, action_type, nome_familia, classe_pvp, ap, aap, dp, linkgear):
    """Envia notificação de registro/atualização para o canal especificado"""
    try:
        channel = bot.get_channel(NOTIFICATION_CHANNEL_ID)
        if not channel:
            # Tentar buscar o canal se não estiver em cache
            channel = await bot.fetch_channel(NOTIFICATION_CHANNEL_ID)
        
        if channel:
            gs_total = calculate_gs(ap, aap, dp)
            
            if action_type == "registro":
                title = "✅ Novo Gearscore Registrado!"
                color = discord.Color.green()
            else:  # atualizar
                title = "🔄 Gearscore Atualizado!"
                color = discord.Color.blue()
            
            embed = discord.Embed(
                title=title,
                color=color,
                timestamp=discord.utils.utcnow()
            )
            embed.add_field(name="👤 Usuário", value=interaction.user.mention, inline=True)
            embed.add_field(name="👤 Família", value=nome_familia, inline=True)
            embed.add_field(name="🎭 Classe PVP", value=classe_pvp, inline=True)
            embed.add_field(name="⚔️ AP", value=f"{ap}", inline=True)
            embed.add_field(name="🔥 AAP", value=f"{aap}", inline=True)
            embed.add_field(name="🛡️ DP", value=f"{dp}", inline=True)
            embed.add_field(name="📊 GS Total", value=f"**{gs_total}** (MAX({ap}, {aap}) + {dp})", inline=False)
            embed.add_field(name="🔗 Link Gear", value=linkgear or "Não informado", inline=False)
            embed.set_footer(text=f"{action_type.capitalize()} por {interaction.user.display_name}")
            
            await channel.send(embed=embed)
            logger.info(f"Notificação enviada ao canal: {action_type} - {nome_familia} ({classe_pvp})")
    except Exception as e:
        # Não interromper o fluxo principal se houver erro ao enviar notificação
        logger.error(f"Erro ao enviar notificação ao canal (ID: {NOTIFICATION_CHANNEL_ID}): {str(e)}")

# Função helper para enviar log de movimentação de membros
async def send_move_log_to_channel(bot, interaction, origin_channel, destination_channel, moved_count, failed_count, failed_members):
    """Envia log de movimentação de membros para o canal de logs"""
    try:
        channel = bot.get_channel(MOVE_LOG_CHANNEL_ID)
        if not channel:
            # Tentar buscar o canal se não estiver em cache
            channel = await bot.fetch_channel(MOVE_LOG_CHANNEL_ID)
        
        if channel:
            embed = discord.Embed(
                title="🔄 Log de Movimentação de Membros",
                description="Registro de movimentação entre salas de voz",
                color=discord.Color.blue(),
                timestamp=discord.utils.utcnow()
            )
            
            embed.add_field(
                name="👤 Executado por",
                value=f"{interaction.user.mention} ({interaction.user.display_name})",
                inline=False
            )
            
            embed.add_field(
                name="📤 Sala de Origem",
                value=f"{origin_channel.mention}\n**ID:** {origin_channel.id}\n**Nome:** {origin_channel.name}",
                inline=True
            )
            
            embed.add_field(
                name="📥 Sala de Destino",
                value=f"{destination_channel.mention}\n**ID:** {destination_channel.id}\n**Nome:** {destination_channel.name}",
                inline=True
            )
            
            embed.add_field(
                name="✅ Membros Movidos",
                value=f"**{moved_count}** membro(s) movidos com sucesso",
                inline=True
            )
            
            if failed_count > 0:
                embed.add_field(
                    name="❌ Falhas",
                    value=f"**{failed_count}** membro(s) não puderam ser movidos",
                    inline=True
                )
                
                # Lista de falhas (limitada a 10 para não exceder limite do embed)
                if failed_members:
                    failed_list = ""
                    for member, reason in failed_members[:10]:
                        failed_list += f"• {member.mention} ({member.display_name}) - {reason}\n"
                    
                    if len(failed_members) > 10:
                        failed_list += f"\n... e mais {len(failed_members) - 10} membro(s)"
                    
                    embed.add_field(
                        name="🚫 Membros que Falharam",
                        value=failed_list,
                        inline=False
                    )
            
            embed.set_footer(text=f"Log gerado automaticamente")
            
            await channel.send(embed=embed)
            logger.info(f"Log de movimentação enviado: {moved_count} membros movidos de {origin_channel.name} para {destination_channel.name}")
    except Exception as e:
        # Não interromper o fluxo principal se houver erro ao enviar log
        logger.error(f"Erro ao enviar log de movimentação ao canal (ID: {MOVE_LOG_CHANNEL_ID}): {str(e)}")

@bot.event
async def on_ready():
    logger.info(f'Bot está online! Usuário: {bot.user} (ID: {bot.user.id})')
    logger.info(f'Bot está em {len(bot.guilds)} servidor(es)')
    
    try:
        synced = await bot.tree.sync()
        logger.info(f'Sincronizados {len(synced)} comando(s) slash')
    except Exception as e:
        logger.error(f'Erro ao sincronizar comandos: {e}')

    # Reativar o botão dos painéis de verificação da guilda já postados
    bot.add_view(GuildCheckPanelView())

    # Log de configuração de administradores
    if ADMIN_USER_IDS:
        logger.info(f'[ADMIN] ADMIN_USER_IDS carregado: {ADMIN_USER_IDS}')
    else:
        logger.info(f'[ADMIN] ADMIN_USER_IDS não configurado ou vazio.')
    
    if ADMIN_ROLE_IDS:
        logger.info(f'[ADMIN] ADMIN_ROLE_IDS carregado: {ADMIN_ROLE_IDS}')
    else:
        logger.info(f'[ADMIN] ADMIN_ROLE_IDS não configurado ou vazio.')
    
    if not ADMIN_USER_IDS and not ADMIN_ROLE_IDS:
        logger.info(f'[ADMIN] Apenas administradores do servidor terão acesso aos comandos de ADMIN.')
    
    # Sincronizar cargos de registro de todos os membros da guilda
    for guild in bot.guilds:
        try:
            await sync_registration_roles(guild)
            logger.info(f'Cargos de registro sincronizados para {guild.name} (ID: {guild.id})')
        except Exception as e:
            logger.error(f'Erro ao sincronizar cargos em {guild.name} (ID: {guild.id}): {e}')
    
    # Iniciar task de lembrete de atualização de GS
    if not gs_reminder_task.is_running():
        gs_reminder_task.start()
        logger.info(f'Task de lembrete de GS iniciada (verificação a cada {GS_UPDATE_REMINDER_DAYS} dias)')
    
    # Iniciar task de reset mensal de eventos
    if not eventos_reset_task.is_running():
        eventos_reset_task.start()
        logger.info('Task de reset mensal de eventos iniciada (executa no dia 1 de cada mês)')

@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
    """Monitora mudanças de cargo dos membros para manter tracking de registro"""
    # Verificar se o membro perdeu o cargo da guilda
    had_guild_role = has_guild_role(before)
    has_guild_role_now = has_guild_role(after)
    
    # Se perdeu o cargo membro, remover cargos de registro
    if had_guild_role and not has_guild_role_now:
        try:
            registered_role = after.guild.get_role(REGISTERED_ROLE_ID)
            unregistered_role = after.guild.get_role(UNREGISTERED_ROLE_ID)
            
            roles_to_remove = []
            if registered_role and registered_role in after.roles:
                roles_to_remove.append(registered_role)
            if unregistered_role and unregistered_role in after.roles:
                roles_to_remove.append(unregistered_role)
            
            if roles_to_remove:
                await after.remove_roles(*roles_to_remove, reason="Perdeu cargo de membro da guilda")
                logger.info(f'Cargos de registro removidos de {after.display_name} (ID: {after.id}) - perdeu cargo membro')
        except Exception as e:
            logger.error(f'Erro ao remover cargos de registro de {after.display_name} (ID: {after.id}): {e}')
    
    # Se ganhou o cargo membro, verificar se precisa do cargo "Não Registrado"
    elif not had_guild_role and has_guild_role_now:
        try:
            # Verificar se tem registro
            user_id = str(after.id)
            user_gear = db.get_gearscore(user_id)
            has_registration = bool(user_gear)
            
            # Atualizar cargos de registro
            await update_registration_roles(after, has_registration)
            status = "com registro" if has_registration else "sem registro"
            logger.info(f'Cargos de registro atualizados para {after.display_name} (ID: {after.id}) - ganhou cargo membro ({status})')
        except Exception as e:
            logger.error(f'Erro ao atualizar cargos de registro de {after.display_name} (ID: {after.id}): {e}')

@bot.event
async def on_message(message: discord.Message):
    # Ignorar mensagens do próprio bot
    if message.author == bot.user:
        return
    
    # Responder a DMs (mensagens privadas)
    if isinstance(message.channel, discord.DMChannel):
        # Verificar se é um comando de texto
        if message.content.lower().startswith('!help'):
            embed = discord.Embed(
                title="🤖 Comandos Disponíveis",
                description="Use comandos slash (/) no servidor ou aqui na DM:",
                color=discord.Color.blue()
            )
            embed.add_field(
                name="📊 Comandos de Gearscore",
                value="`/atualizar_gearscore` - Atualiza seu gearscore\n"
                      "`/perfil` - Visualiza seu perfil completo\n"
                      "`/gearscore_dm` - Recebe gearscore via DM\n"
                      "`/ranking_gearscore` - Ver ranking\n"
                      "`/estatisticas_classes` - Estatísticas das classes",
                inline=False
            )
            embed.add_field(
                name="💡 Dica",
                value="Use os comandos slash (/) digitando `/` no Discord!",
                inline=False
            )
            await message.channel.send(embed=embed)
        elif message.content.lower().startswith('!oi') or message.content.lower().startswith('!ola'):
            await message.channel.send(f"Olá {message.author.mention}! 👋\nUse `/gearscore_dm` para receber seu gearscore via DM ou `/help` para ver todos os comandos!")
        else:
            # Responder a outras mensagens na DM
            await message.channel.send(
                f"Olá {message.author.mention}! 👋\n"
                "Use `/gearscore_dm` para receber seu gearscore via DM.\n"
                "Ou use `!help` para ver todos os comandos disponíveis."
            )
    
    # Processar comandos de prefixo (!) em servidores
    await bot.process_commands(message)

# Autocomplete para classe PVP (com tratamento de erro para evitar spam de logs)
async def classe_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    """Autocomplete para classes do BDO"""
    try:
        # Filtrar classes que começam com o texto digitado (case-insensitive)
        filtered = [
            classe for classe in BDO_CLASSES 
            if current.lower() in classe.lower()
        ][:25]  # Limitar a 25 resultados
        return [app_commands.Choice(name=classe, value=classe) for classe in filtered]
    except Exception:
        # Se der erro (interação expirada), retornar lista vazia silenciosamente
        return []

@bot.tree.command(name="registro", description="Registra seu gearscore pela primeira vez")
@app_commands.describe(
    nome_familia="Nome da família do personagem",
    nome_personagem="Nome do personagem",
    classe_pvp="Classe PVP do personagem (digite para buscar)",
    ap="Attack Power (AP)",
    aap="Awakened Attack Power (AAP)",
    dp="Defense Power (DP)",
    linkgear="Link do gear (opcional)"
)
@app_commands.autocomplete(classe_pvp=classe_autocomplete)
async def registro(
    interaction: discord.Interaction,
    nome_familia: str,
    nome_personagem: str,
    classe_pvp: str,
    ap: int,
    aap: int,
    dp: int,
    linkgear: str = None
):
    # Deferir resposta IMEDIATAMENTE para evitar timeout
    try:
        await interaction.response.defer(ephemeral=True)
    except discord.errors.NotFound:
        # Interação já expirou, tentar enviar mensagem direta
        try:
            await interaction.followup.send(
                "⏱️ A interação expirou. Por favor, tente novamente.",
                ephemeral=True
            )
        except:
            pass
        return
    except Exception as e:
        logger.error(f"Erro ao defer interação: {e}")
        try:
            await interaction.followup.send(
                "❌ Erro ao processar comando. Por favor, tente novamente.",
                ephemeral=True
            )
        except:
            pass
        return
    
    try:
        # Validar valores numéricos
        if ap < 0 or aap < 0 or dp < 0:
            await interaction.followup.send(
                "❌ Os valores de AP, AAP e DP devem ser números positivos!",
                ephemeral=True
            )
            return
        
        # Linkgear é opcional (a coluna no banco não aceita nulo, então salva vazio)
        linkgear = (linkgear or "").strip()
        
        # Validar classe PVP
        if classe_pvp not in BDO_CLASSES:
            classes_str = ", ".join(BDO_CLASSES[:10])
            await interaction.followup.send(
                f"❌ Classe inválida! Classes disponíveis: {classes_str}... (use autocomplete para ver todas)",
                ephemeral=True
            )
            return
        
        user_id = str(interaction.user.id)
        
        # Verificar se é em um servidor (não DM)
        if not interaction.guild:
            await interaction.followup.send(
                "❌ Este comando só pode ser usado em um servidor!",
                ephemeral=True
            )
            return
        
        # Registrar gearscore
        logger.info(f"Comando /registro executado por {interaction.user.display_name} (ID: {interaction.user.id}) - {nome_familia} ({classe_pvp}) - GS: {calculate_gs(ap, aap, dp)}")
        db.register_gearscore(
            user_id=user_id,
            family_name=nome_familia,
            character_name=nome_personagem,
            class_pvp=classe_pvp,
            ap=ap,
            aap=aap,
            dp=dp,
            linkgear=linkgear
        )
        
        # Adicionar cargo da guilda ao membro (se não tiver)
        member = interaction.guild.get_member(interaction.user.id)
        role_added = False
        role_error = None
        
        if member:
            guild_role = interaction.guild.get_role(GUILD_MEMBER_ROLE_ID)
            if guild_role:
                try:
                    if not has_guild_role(member):
                        await member.add_roles(guild_role, reason="Registro de gearscore - membro da guilda")
                        role_added = True
                except discord.Forbidden:
                    role_error = "Sem permissão para adicionar cargo"
                except discord.HTTPException as e:
                    role_error = f"Erro ao adicionar cargo: {str(e)}"
            else:
                role_error = "Cargo da guilda não encontrado no servidor"
        else:
            role_error = "Membro não encontrado no servidor"
        
        # Atualizar cargos de registro (dar "Registrado" e remover "Não Registrado")
        if member:
            await update_registration_roles(member, has_registration=True)
        
        # Atualizar nickname para o nome de família
        nickname_updated = False
        nickname_error = None
        if member:
            logger.info(f"[/registro] Tentando atualizar nickname de {member.display_name} (ID: {member.id}) para '{nome_familia}'")
            nick_success, nick_msg = await update_member_nickname(member, nome_familia)
            if nick_success:
                nickname_updated = True
                logger.info(f"[/registro] Nickname atualizado com sucesso para {member.display_name}")
            else:
                nickname_error = nick_msg
                logger.warning(f"[/registro] Falha ao atualizar nickname de {member.display_name}: {nick_msg}")
        else:
            logger.warning(f"[/registro] Member não encontrado para atualizar nickname")
        
        # Calcular GS total (MAX(AP, AAP) + DP)
        gs_total = calculate_gs(ap, aap, dp)
        
        # Buscar posição no ranking
        ranking_info = await get_player_ranking_position(interaction.guild, user_id, gs_total)
        
        embed = discord.Embed(
            title="✅ Gearscore Registrado!",
            color=discord.Color.green(),
            timestamp=discord.utils.utcnow()
        )
        embed.add_field(name="👤 Família", value=nome_familia, inline=True)
        embed.add_field(name="👤 Personagem", value=nome_personagem, inline=True)
        embed.add_field(name="🎭 Classe PVP", value=classe_pvp, inline=True)
        embed.add_field(name="⚔️ AP", value=f"{ap}", inline=True)
        embed.add_field(name="🔥 AAP", value=f"{aap}", inline=True)
        embed.add_field(name="🛡️ DP", value=f"{dp}", inline=True)
        embed.add_field(name="📊 GS Total", value=f"**{gs_total}** (MAX({ap}, {aap}) + {dp})", inline=False)
        
        # Adicionar informação de ranking (gamificação)
        if ranking_info:
            posicao = ranking_info['posicao']
            
            # Mensagem simplificada
            if posicao <= 3:
                ranking_value = f"🎉 **Parabéns! Você está no top 3 pessoas mais fortes da aliança!** 🏆"
            else:
                ranking_value = f"🎉 **Parabéns! Você está em {posicao}º lugar na aliança!**"
            
            embed.add_field(name="🏆 Seu Ranking", value=ranking_value, inline=False)
        
        embed.add_field(name="🔗 Link Gear", value=linkgear or "Não informado", inline=False)
        
        if role_added:
            embed.add_field(name="🎖️ Cargo", value="Cargo da guilda atribuído com sucesso!", inline=False)
        elif role_error:
            embed.add_field(name="⚠️ Aviso", value=f"Não foi possível adicionar o cargo: {role_error}", inline=False)
        
        if nickname_updated:
            embed.add_field(name="✏️ Nickname", value=f"Seu apelido foi atualizado para **{nome_familia}**", inline=False)
        elif nickname_error:
            embed.add_field(name="⚠️ Nickname", value=f"Não foi possível atualizar o apelido: {nickname_error}", inline=False)
        
        embed.set_footer(text=f"Registrado por {interaction.user.display_name}")
        
        await interaction.followup.send(embed=embed, ephemeral=True)
        
        # Enviar notificação ao canal
        await send_notification_to_channel(
            bot, interaction, "registro", 
            nome_familia, classe_pvp, ap, aap, dp, linkgear
        )
    except ValueError as e:
        logger.error(f"Erro de validação no /registro: {e}")
        await interaction.followup.send(f"❌ {str(e)}", ephemeral=True)
    except Exception as e:
        logger.error(f"Erro no comando /registro: {e}")
        await interaction.followup.send(f"❌ Erro ao registrar gearscore: {str(e)}", ephemeral=True)

@bot.tree.command(name="registro_manual", description="[ADMIN] Registra gearscore manualmente para outro membro")
@app_commands.describe(
    usuario="Usuário do Discord para registrar",
    nome_familia="Nome da família do personagem",
    nome_personagem="Nome do personagem",
    classe_pvp="Classe PVP do personagem (digite para buscar)",
    ap="Attack Power (AP)",
    aap="Awakened Attack Power (AAP)",
    dp="Defense Power (DP)",
    linkgear="Link do gear (obrigatório)"
)
@app_commands.autocomplete(classe_pvp=classe_autocomplete)
async def registro_manual(
    interaction: discord.Interaction,
    usuario: discord.Member,
    nome_familia: str,
    nome_personagem: str,
    classe_pvp: str,
    ap: int,
    aap: int,
    dp: int,
    linkgear: str
):
    """Registra gearscore manualmente para outro membro (apenas administradores)"""
    if not is_admin_user(interaction.user):
        await interaction.response.send_message(
            "❌ Apenas administradores podem usar este comando!",
            ephemeral=True
        )
        return
    
    # Validar valores numéricos
    if ap < 0 or aap < 0 or dp < 0:
        await interaction.response.send_message(
            "❌ Os valores de AP, AAP e DP devem ser números positivos!",
            ephemeral=True
        )
        return
    
    # Validar linkgear
    if not linkgear or linkgear.strip() == "":
        await interaction.response.send_message(
            "❌ O link do gear é obrigatório!",
            ephemeral=True
        )
        return
    
    # Validar classe PVP
    if classe_pvp not in BDO_CLASSES:
        classes_str = ", ".join(BDO_CLASSES[:10])  # Mostrar primeiras 10
        await interaction.response.send_message(
            f"❌ Classe inválida! Classes disponíveis: {classes_str}... (use autocomplete para ver todas)",
            ephemeral=True
        )
        return
    
    try:
        # Verificar se é em um servidor (não DM)
        if not interaction.guild:
            await interaction.response.send_message(
                "❌ Este comando só pode ser usado em um servidor!",
                ephemeral=True
            )
            return
        
        # Deferir resposta se a operação pode demorar
        await interaction.response.defer(ephemeral=True)
        
        target_user_id = str(usuario.id)
        
        # Registrar gearscore para o usuário selecionado
        logger.info(f"Comando /registro_manual executado por {interaction.user.display_name} (ID: {interaction.user.id}) para {usuario.display_name} (ID: {target_user_id}) - {nome_familia} ({classe_pvp}) - GS: {calculate_gs(ap, aap, dp)}")
        db.register_gearscore(
            user_id=target_user_id,
            family_name=nome_familia,
            character_name=nome_personagem,
            class_pvp=classe_pvp,
            ap=ap,
            aap=aap,
            dp=dp,
            linkgear=linkgear
        )
        
        # Adicionar cargo da guilda ao membro selecionado (se não tiver)
        member = interaction.guild.get_member(usuario.id)
        role_added = False
        role_error = None
        
        if member:
            guild_role = interaction.guild.get_role(GUILD_MEMBER_ROLE_ID)
            if guild_role:
                try:
                    if not has_guild_role(member):
                        await member.add_roles(guild_role, reason=f"Registro manual de gearscore por {interaction.user.display_name}")
                        role_added = True
                except discord.Forbidden:
                    role_error = "Sem permissão para adicionar cargo"
                except discord.HTTPException as e:
                    role_error = f"Erro ao adicionar cargo: {str(e)}"
            else:
                role_error = "Cargo da guilda não encontrado no servidor"
        else:
            role_error = "Membro não encontrado no servidor"
        
        # Atualizar cargos de registro (dar "Registrado" e remover "Não Registrado")
        if member:
            await update_registration_roles(member, has_registration=True)
        
        # Atualizar nickname para o nome de família
        nickname_updated = False
        nickname_error = None
        if member:
            nick_success, nick_msg = await update_member_nickname(member, nome_familia)
            if nick_success:
                nickname_updated = True
            else:
                nickname_error = nick_msg
        
        # Calcular GS total (MAX(AP, AAP) + DP)
        gs_total = calculate_gs(ap, aap, dp)
        
        embed = discord.Embed(
            title="✅ Gearscore Registrado Manualmente!",
            color=discord.Color.green(),
            timestamp=discord.utils.utcnow()
        )
        embed.add_field(name="👤 Usuário", value=usuario.mention, inline=True)
        embed.add_field(name="👤 Família", value=nome_familia, inline=True)
        embed.add_field(name="👤 Personagem", value=nome_personagem, inline=True)
        embed.add_field(name="🎭 Classe PVP", value=classe_pvp, inline=True)
        embed.add_field(name="⚔️ AP", value=f"{ap}", inline=True)
        embed.add_field(name="🔥 AAP", value=f"{aap}", inline=True)
        embed.add_field(name="🛡️ DP", value=f"{dp}", inline=True)
        embed.add_field(name="📊 GS Total", value=f"**{gs_total}** (MAX({ap}, {aap}) + {dp})", inline=False)
        embed.add_field(name="🔗 Link Gear", value=linkgear, inline=False)
        
        if role_added:
            embed.add_field(name="🎖️ Cargo", value="Cargo da guilda atribuído com sucesso!", inline=False)
        elif role_error:
            embed.add_field(name="⚠️ Aviso", value=f"Não foi possível adicionar o cargo: {role_error}", inline=False)
        
        if nickname_updated:
            embed.add_field(name="✏️ Nickname", value=f"Apelido atualizado para **{nome_familia}**", inline=False)
        elif nickname_error:
            embed.add_field(name="⚠️ Nickname", value=f"Não foi possível atualizar o apelido: {nickname_error}", inline=False)
        
        embed.set_footer(text=f"Registrado manualmente por {interaction.user.display_name}")
        
        await interaction.followup.send(embed=embed, ephemeral=True)
        
        # Enviar notificação ao canal
        try:
            channel = bot.get_channel(NOTIFICATION_CHANNEL_ID)
            if not channel:
                channel = await bot.fetch_channel(NOTIFICATION_CHANNEL_ID)
            
            if channel:
                gs_total = calculate_gs(ap, aap, dp)
                
                embed = discord.Embed(
                    title="✅ Novo Gearscore Registrado Manualmente!",
                    color=discord.Color.green(),
                    timestamp=discord.utils.utcnow()
                )
                embed.add_field(name="👤 Usuário", value=usuario.mention, inline=True)
                embed.add_field(name="👤 Família", value=nome_familia, inline=True)
                embed.add_field(name="👤 Personagem", value=nome_personagem, inline=True)
                embed.add_field(name="🎭 Classe PVP", value=classe_pvp, inline=True)
                embed.add_field(name="⚔️ AP", value=f"{ap}", inline=True)
                embed.add_field(name="🔥 AAP", value=f"{aap}", inline=True)
                embed.add_field(name="🛡️ DP", value=f"{dp}", inline=True)
                embed.add_field(name="📊 GS Total", value=f"**{gs_total}** (MAX({ap}, {aap}) + {dp})", inline=False)
                embed.add_field(name="🔗 Link Gear", value=linkgear, inline=False)
                embed.set_footer(text=f"Registrado manualmente por {interaction.user.display_name}")
                
                await channel.send(embed=embed)
        except Exception as e:
            logger.error(f"Erro ao enviar notificação ao canal (ID: {NOTIFICATION_CHANNEL_ID}): {e}")
        
        # Enviar DM para o usuário informando sobre o registro manual
        try:
            dm_embed = discord.Embed(
                title="✅ Gearscore Registrado",
                description=f"Seu gearscore foi registrado manualmente por um administrador.",
                color=discord.Color.blue(),
                timestamp=discord.utils.utcnow()
            )
            dm_embed.add_field(name="👤 Família", value=nome_familia, inline=True)
            dm_embed.add_field(name="👤 Personagem", value=nome_personagem, inline=True)
            dm_embed.add_field(name="🎭 Classe PVP", value=classe_pvp, inline=True)
            dm_embed.add_field(name="📊 GS Total", value=f"**{gs_total}**", inline=False)
            dm_embed.set_footer(text="Use /perfil para ver seu perfil completo")
            await usuario.send(embed=dm_embed)
        except discord.Forbidden:
            # Usuário bloqueou DMs, não é problema
            pass
        except Exception as e:
            # Erro ao enviar DM, não é crítico
            logger.warning(f"Erro ao enviar DM para usuário (ID: {usuario.id}): {e}")
        
    except ValueError as e:
        # Verificar se já respondeu
        if interaction.response.is_done():
            await interaction.followup.send(
                f"❌ {str(e)}",
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"❌ {str(e)}",
                ephemeral=True
            )
    except Exception as e:
        # Verificar se já respondeu
        if interaction.response.is_done():
            await interaction.followup.send(
                f"❌ Erro ao registrar gearscore: {str(e)}",
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"❌ Erro ao registrar gearscore: {str(e)}",
                ephemeral=True
            )

@bot.tree.command(name="atualizar", description="Atualiza seu gearscore (pode mudar de classe)")
@app_commands.describe(
    ap="Attack Power (AP) - Obrigatório",
    aap="Awakened Attack Power (AAP) - Obrigatório",
    dp="Defense Power (DP) - Obrigatório",
    linkgear="Link do gear (opcional - se não informar, mantém o atual)",
    nome_familia="Nome da família do personagem (opcional se já cadastrado)",
    nome_personagem="Nome do personagem (opcional se já cadastrado)",
    classe_pvp="Classe PVP do personagem (opcional se já cadastrado, digite para buscar)"
)
@app_commands.autocomplete(classe_pvp=classe_autocomplete)
async def atualizar(
    interaction: discord.Interaction,
    ap: int,
    aap: int,
    dp: int,
    linkgear: str = None,
    nome_familia: str = None,
    nome_personagem: str = None,
    classe_pvp: str = None
):
    # Deferir resposta IMEDIATAMENTE para evitar timeout
    deferred = False
    try:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)
            deferred = True
    except discord.errors.NotFound:
        # Interação já expirou, mas vamos tentar continuar
        logger.warning("Interação expirada ao tentar defer no /atualizar, mas continuando...")
        deferred = False
    except Exception as e:
        logger.error(f"Erro ao defer interação no /atualizar: {e}")
        # Tentar continuar mesmo se o defer falhar
        deferred = False
    
    try:
        # Validar valores numéricos
        if ap < 0 or aap < 0 or dp < 0:
            await interaction.followup.send(
                "❌ Os valores de AP, AAP e DP devem ser números positivos!",
                ephemeral=True
            )
            return
        
        # Linkgear é opcional: se não informado, mantém o link já cadastrado
        if linkgear is not None and linkgear.strip() == "":
            linkgear = None
        
        user_id = str(interaction.user.id)

        # Resolver a guilda (interaction.guild é None quando o comando é usado via DM)
        guild, guild_error = await resolve_interaction_guild(interaction)
        if not guild:
            logger.warning(f"/atualizar via DM negado para {interaction.user} (ID: {user_id}): {guild_error}")
            await interaction.followup.send(guild_error, ephemeral=True)
            return

        # Verificar se já existe registro
        current_data = db.get_user_current_data(user_id)
        if not current_data:
            await interaction.followup.send(
                "❌ Você ainda não possui um registro! Use `/registro` primeiro.",
                ephemeral=True
            )
            return
        
        current_family_name, current_character_name, current_class_pvp = current_data
        
        # Validar classe PVP se fornecida
        if classe_pvp and classe_pvp not in BDO_CLASSES:
            classes_str = ", ".join(BDO_CLASSES[:10])
            await interaction.followup.send(
                f"❌ Classe inválida! Classes disponíveis: {classes_str}... (use autocomplete para ver todas)",
                ephemeral=True
            )
            return
        
        # Se não forneceu classe_pvp, usar a atual
        if classe_pvp is None:
            classe_pvp = current_class_pvp
        
        # Se não forneceu nome_familia, usar o atual
        if nome_familia is None:
            nome_familia = current_family_name
        
        # Se mudou de classe, o nome do personagem é OBRIGATÓRIO
        if classe_pvp != current_class_pvp:
            if nome_personagem is None or nome_personagem.strip() == "":
                # Tentar enviar DM (sem bloquear)
                try:
                    dm_embed = discord.Embed(
                        title="⚠️ Nome do Personagem Obrigatório",
                        description=f"Você está mudando de classe de **{current_class_pvp}** para **{classe_pvp}**.\n\n"
                                   f"Como você está mudando para um personagem diferente, é **obrigatório** fornecer o nome do novo personagem.\n\n"
                                   f"Por favor, use o comando `/atualizar` novamente incluindo o parâmetro `nome_personagem`.",
                        color=discord.Color.orange(),
                        timestamp=discord.utils.utcnow()
                    )
                    dm_embed.add_field(
                        name="📝 Exemplo",
                        value=f"`/atualizar ap:300 aap:280 dp:400 nome_personagem:NovoNome classe_pvp:{classe_pvp}`",
                        inline=False
                    )
                    await interaction.user.send(embed=dm_embed)
                except:
                    pass
                
                await interaction.followup.send(
                    f"❌ **Nome do personagem obrigatório!**\n\n"
                    f"Você está mudando de classe de **{current_class_pvp}** para **{classe_pvp}**.\n"
                    f"Como você está mudando para um personagem diferente, é **obrigatório** fornecer o nome do novo personagem.\n\n"
                    f"**Exemplo:** `/atualizar ap:{ap} aap:{aap} dp:{dp} nome_personagem:NovoNome classe_pvp:{classe_pvp}`",
                    ephemeral=True
                )
                return
        
        # Se não mudou de classe e não forneceu nome_personagem, manter o atual
        if nome_personagem is None:
            nome_personagem = current_character_name
        
        # Buscar GS anterior antes de atualizar (apenas para mostrar diferença)
        old_gs_data = db.get_gearscore(user_id)
        old_gs = None
        if old_gs_data:
            result = old_gs_data[0]
            if isinstance(result, dict):
                old_ap = result.get('ap', 0)
                old_aap = result.get('aap', 0)
                old_dp = result.get('dp', 0)
            else:
                old_ap = result[5] if len(result) > 5 else 0
                old_aap = result[6] if len(result) > 6 else 0
                old_dp = result[7] if len(result) > 7 else 0
            old_gs = calculate_gs(old_ap, old_aap, old_dp)
            
            # Se não informou o link, manter o já cadastrado
            if linkgear is None:
                if isinstance(result, dict):
                    linkgear = result.get('linkgear')
                else:
                    linkgear = result[8] if len(result) > 8 else None
        
        # Atualizar gearscore PRIMEIRO (mais rápido)
        logger.info(f"Comando /atualizar executado por {interaction.user.display_name} (ID: {user_id}) - {nome_familia} ({classe_pvp}) - GS: {calculate_gs(ap, aap, dp)}")
        db.update_gearscore(
            user_id=user_id,
            family_name=nome_familia,
            character_name=nome_personagem,
            class_pvp=classe_pvp,
            ap=ap,
            aap=aap,
            dp=dp,
            linkgear=linkgear
        )
        logger.info(f"Gearscore atualizado com sucesso para {interaction.user.display_name} (ID: {user_id})")
        
        # Atualizar nickname se o nome de família mudou
        nickname_updated = False
        nickname_error = None
        member = guild.get_member(interaction.user.id)
        if member and nome_familia != current_family_name:
            nick_success, nick_msg = await update_member_nickname(member, nome_familia)
            if nick_success:
                nickname_updated = True
            else:
                nickname_error = nick_msg
        
        # Calcular GS total
        gs_total = calculate_gs(ap, aap, dp)
        
        # Buscar ranking atual (após atualização) e antigo (se necessário para comparação)
        new_ranking = None
        old_ranking = None
        try:
            logger.info(f"Buscando ranking para user_id={user_id}, gs_total={gs_total}")
            new_ranking = await get_player_ranking_position(guild, user_id, gs_total)
            logger.info(f"Ranking encontrado: {new_ranking}")
            
            # Buscar ranking antigo apenas se o GS mudou e queremos mostrar a diferença
            if old_gs is not None and old_gs != gs_total:
                try:
                    old_ranking = await get_player_ranking_position(guild, user_id, old_gs)
                    logger.info(f"Ranking antigo encontrado: {old_ranking}")
                except Exception as e:
                    logger.warning(f"Erro ao buscar ranking antigo: {e}")
                    old_ranking = None
        except Exception as e:
            logger.error(f"Erro ao buscar ranking: {e}", exc_info=True)
            new_ranking = None
            old_ranking = None
        
        embed = discord.Embed(
            title="✅ Gearscore Atualizado!",
            color=discord.Color.green(),
            timestamp=discord.utils.utcnow()
        )
        embed.add_field(name="👤 Família", value=nome_familia, inline=True)
        if nome_personagem:
            embed.add_field(name="👤 Personagem", value=nome_personagem, inline=True)
        embed.add_field(name="🎭 Classe PVP", value=classe_pvp, inline=True)
        embed.add_field(name="⚔️ AP", value=f"{ap}", inline=True)
        embed.add_field(name="🔥 AAP", value=f"{aap}", inline=True)
        embed.add_field(name="🛡️ DP", value=f"{dp}", inline=True)
        embed.add_field(name="📊 GS Total", value=f"**{gs_total}** (MAX({ap}, {aap}) + {dp})", inline=False)
        
        # Mostrar mudança de GS se houver
        if old_gs is not None and old_gs != gs_total:
            gs_diff = gs_total - old_gs
            if gs_diff > 0:
                gs_change = f"📈 **+{gs_diff} GS** (era {old_gs})"
            else:
                gs_change = f"📉 **{gs_diff} GS** (era {old_gs})"
            embed.add_field(name="🔄 Mudança de GS", value=gs_change, inline=False)
        
        # Adicionar informação de ranking (gamificação)
        if new_ranking:
            posicao = new_ranking['posicao']
            
            # Mensagem simplificada
            if posicao <= 3:
                ranking_value = f"🎉 **Parabéns! Você está no top 3 pessoas mais fortes da aliança!** 🏆"
            else:
                ranking_value = f"🎉 **Parabéns! Você está em {posicao}º lugar na aliança!**"
            
            # Mostrar mudança de posição se subiu
            if old_ranking and old_ranking['posicao'] != posicao:
                pos_diff = old_ranking['posicao'] - posicao
                if pos_diff > 0:
                    ranking_value += f"\n\n🎯 **Subiu {pos_diff} posição(ões)!** ⬆️"
            
            embed.add_field(name="🏆 Seu Ranking", value=ranking_value, inline=False)
        else:
            # Se o ranking não estiver disponível, adicionar mensagem informativa
            logger.warning(f"Ranking não disponível para user_id={user_id}")
            # Verificar se o usuário tem o cargo da guilda
            member_check = guild.get_member(interaction.user.id)
            if member_check and has_guild_role(member_check):
                embed.add_field(
                    name="🏆 Seu Ranking", 
                    value="⚠️ Ranking temporariamente indisponível. Tente novamente em alguns instantes.",
                    inline=False
                )
        
        embed.add_field(name="🔗 Link Gear", value=linkgear or "Não informado", inline=False)
        
        if current_class_pvp != classe_pvp:
            embed.add_field(
                name="🔄 Mudança de Classe",
                value=f"Classe alterada de **{current_class_pvp}** para **{classe_pvp}**",
                inline=False
            )
        
        if nickname_updated:
            embed.add_field(name="✏️ Nickname", value=f"Seu apelido foi atualizado para **{nome_familia}**", inline=False)
        elif nickname_error:
            embed.add_field(name="⚠️ Nickname", value=f"Não foi possível atualizar o apelido: {nickname_error}", inline=False)
        
        embed.set_footer(text=f"Atualizado por {interaction.user.display_name}")
        
        await interaction.followup.send(embed=embed, ephemeral=True)
        
        # Enviar notificação ao canal
        await send_notification_to_channel(
            bot, interaction, "atualizar", 
            nome_familia, classe_pvp, ap, aap, dp, linkgear
        )
    except Exception as e:
        logger.error(f"Erro no comando /atualizar: {e}")
        await interaction.followup.send(
            f"❌ Erro ao atualizar gearscore: {str(e)}",
            ephemeral=True
        )

# Função auxiliar para gerar perfil (reutilizável)
async def generate_profile_embed(interaction: discord.Interaction, target_user: discord.Member, target_user_id: str = None):
    """Gera o embed do perfil de um usuário"""
    if target_user_id is None:
        target_user_id = str(target_user.id)
    
    results = db.get_gearscore(target_user_id)
    
    if not results:
        return None
    
    # Agora só pode ter 1 resultado (1 classe por usuário)
    result = results[0]
    
    # Formatar dados dependendo do banco
    if isinstance(result, dict):
        family_name = result.get('family_name', 'N/A')
        character_name = result.get('character_name', family_name)
        class_pvp = result.get('class_pvp', 'N/A')
        ap = result.get('ap', 0)
        aap = result.get('aap', 0)
        dp = result.get('dp', 0)
        linkgear = result.get('linkgear', 'N/A')
        updated_at = result.get('updated_at', 'N/A')
    else:
        # Ordem das colunas: id(0), user_id(1), family_name(2), character_name(3), class_pvp(4), ap(5), aap(6), dp(7), linkgear(8), updated_at(9)
        family_name = result[2] if len(result) > 2 else 'N/A'
        character_name = result[3] if len(result) > 3 else family_name
        class_pvp = result[4] if len(result) > 4 else 'N/A'
        ap = result[5] if len(result) > 5 else 0
        aap = result[6] if len(result) > 6 else 0
        dp = result[7] if len(result) > 7 else 0
        linkgear = result[8] if len(result) > 8 else 'N/A'
        updated_at = result[9] if len(result) > 9 else 'N/A'
    
    gs_total = calculate_gs(ap, aap, dp)
    
    # Buscar histórico para verificar se foi criado ou atualizado
    try:
        history = db.get_user_history(target_user_id, class_pvp)
        is_created = len(history) == 1 if history else True
    except:
        is_created = False
    
    # Formatar data
    def format_date(date_str):
        """Formata data para DD/MM/YYYY - HH:MM"""
        try:
            if isinstance(date_str, str) and date_str != 'N/A':
                from datetime import datetime
                formats = [
                    '%Y-%m-%d %H:%M:%S',
                    '%Y-%m-%d %H:%M:%S.%f',
                    '%Y-%m-%dT%H:%M:%S',
                    '%Y-%m-%dT%H:%M:%S.%f',
                    '%Y-%m-%dT%H:%M:%S.%fZ'
                ]
                for fmt in formats:
                    try:
                        dt = datetime.strptime(date_str, fmt)
                        return dt.strftime('%d/%m/%Y - %H:%M')
                    except:
                        continue
                return date_str
            elif hasattr(date_str, 'strftime'):
                return date_str.strftime('%d/%m/%Y - %H:%M')
            return str(date_str)
        except:
            return str(date_str) if date_str else 'N/A'
    
    date_label = "Criado em" if is_created else "Atualizado em"
    formatted_date = format_date(updated_at)
    
    # Buscar membros da guilda para calcular ranking e médias
    valid_user_ids = await get_guild_member_ids(interaction.guild)
    all_gearscores = db.get_all_gearscores(valid_user_ids=valid_user_ids)
    
    # Calcular ranking
    def get_gs_from_result(result):
        if isinstance(result, dict):
            ap_val = result.get('ap', 0)
            aap_val = result.get('aap', 0)
            dp_val = result.get('dp', 0)
        else:
            # Ordem das colunas: id(0), user_id(1), family_name(2), character_name(3), class_pvp(4), ap(5), aap(6), dp(7), linkgear(8), updated_at(9)
            ap_val = result[5] if len(result) > 5 else 0
            aap_val = result[6] if len(result) > 6 else 0
            dp_val = result[7] if len(result) > 7 else 0
        return calculate_gs(ap_val, aap_val, dp_val)
    
    sorted_gearscores = sorted(all_gearscores, key=get_gs_from_result, reverse=True)
    
    # Encontrar posição no ranking
    ranking_position = None
    for idx, gs_result in enumerate(sorted_gearscores, 1):
        if isinstance(gs_result, dict):
            gs_user_id = str(gs_result.get('user_id', ''))
        else:
            gs_user_id = str(gs_result[1] if len(gs_result) > 1 else '')
        
        if gs_user_id == target_user_id:
            ranking_position = idx
            break
    
    # Buscar estatísticas da guilda
    stats = db.get_class_statistics(valid_user_ids=valid_user_ids)
    
    # Calcular média geral (Mouz)
    total_chars = 0
    total_weighted_gs = 0
    class_avg_gs = 0
    
    for stat in stats:
        if isinstance(stat, dict):
            class_name = stat.get('class_pvp', 'Desconhecida')
            total = stat.get('total', 0)
            avg_gs = stat.get('avg_gs', 0)
        else:
            class_name = stat[0]
            total = stat[1]
            avg_gs = float(stat[2]) if len(stat) > 2 and stat[2] is not None else 0
        
        total_chars += total
        total_weighted_gs += avg_gs * total
        
        # Buscar média da classe específica
        if class_name.lower() == class_pvp.lower():
            class_avg_gs = avg_gs
    
    overall_avg_gs = int(round(total_weighted_gs / total_chars)) if total_chars > 0 else 0
    class_avg_gs_int = int(round(class_avg_gs)) if class_avg_gs > 0 else 0
    
    # Comparar com médias
    media_mouz_status = "Acima" if gs_total >= overall_avg_gs else "Abaixo"
    media_classe_status = "Acima" if gs_total >= class_avg_gs_int else "Abaixo"
    
    # Criar embed com layout similar à imagem
    embed = discord.Embed(
        title=f"{family_name}",
        color=discord.Color.blue(),
        timestamp=discord.utils.utcnow()
    )
    
    # Adicionar avatar do usuário
    embed.set_thumbnail(url=target_user.display_avatar.url)
    
    # Coluna esquerda
    embed.add_field(
        name="📄 Família",
        value=family_name,
        inline=True
    )
    
    embed.add_field(
        name="👤 Personagem",
        value=character_name,
        inline=True
    )
    
    embed.add_field(
        name="🏛️ Guilda",
        value=interaction.guild.name,
        inline=True
    )
    
    # Nova linha - Classe e AP/AAP
    embed.add_field(
        name="⚔️ Classe PvP",
        value=class_pvp,
        inline=True
    )
    
    embed.add_field(
        name="⚔️ AP Pre/Succ",
        value=str(ap),
        inline=True
    )
    
    embed.add_field(
        name="🔥 AP Awakening",
        value=str(aap),
        inline=True
    )
    
    # Nova linha - DP, GS e Ranking
    embed.add_field(
        name="🛡️ DP",
        value=str(dp),
        inline=True
    )
    
    embed.add_field(
        name="🏆 Gearscore",
        value=f"**{gs_total}**",
        inline=True
    )
    
    if ranking_position:
        embed.add_field(
            name="📊 Posição GS",
            value=f"**{ranking_position}°**",
            inline=True
        )
    else:
        embed.add_field(
            name="📊 Posição GS",
            value="N/A",
            inline=True
        )
    
    # Nova linha - Médias
    embed.add_field(
        name="📊 Média Mouz",
        value=f"{media_mouz_status} ✅" if media_mouz_status == "Acima" else f"{media_mouz_status} ❌",
        inline=True
    )
    
    embed.add_field(
        name=f"📊 Média ({class_pvp})",
        value=f"{media_classe_status} ✅" if media_classe_status == "Acima" else f"{media_classe_status} ❌",
        inline=True
    )
    
    embed.add_field(
        name="🔗 Link Gear",
        value=f"[Clique aqui]({linkgear})" if linkgear != 'N/A' and linkgear.startswith('http') else (linkgear or "Não informado"),
        inline=True
    )
    
    # Footer com informações resumidas
    footer_text = f"{class_pvp} {gs_total}gs | {date_label} {formatted_date}"
    embed.set_footer(text=footer_text)
    
    return embed

@bot.tree.command(name="perfil", description="Visualiza o seu perfil completo de gearscore")
async def perfil(interaction: discord.Interaction):
    try:
        # Verificar se é em um servidor
        if not interaction.guild:
            await interaction.response.send_message(
                "❌ Este comando só pode ser usado em um servidor!",
                ephemeral=True
            )
            return
        
        await interaction.response.defer(ephemeral=True)
        
        # Gerar perfil do próprio usuário
        embed = await generate_profile_embed(interaction, interaction.user)
        
        if embed is None:
            await interaction.followup.send(
                "❌ Nenhum gearscore encontrado! Use `/registro` para registrar seu gearscore.",
                ephemeral=True
            )
            return
        
        await interaction.followup.send(embed=embed, ephemeral=True)
            
    except Exception as e:
        if interaction.response.is_done():
            await interaction.followup.send(
                f"❌ Erro ao buscar perfil: {str(e)}",
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"❌ Erro ao buscar perfil: {str(e)}",
                ephemeral=True
            )

@bot.tree.command(name="hp_efetivo", description="Mostra quanto de vida você REALMENTE tem contra um inimigo e se vale subir DR ou Evasão")
@app_commands.describe(
    vida="Sua vida máxima (HP)",
    dr="Sua DR (Redução de Dano), aparece na janela de atributos",
    evasao="Sua Evasão, aparece na janela de atributos",
    reducao_dano="Redução de dano em %. Se não sabe, deixe vazio (usa 30%)",
    ap_inimigo="AP do inimigo. Se não sabe, deixe vazio (usa 1100)",
    precisao_inimigo="Precisão do inimigo. Se não sabe, deixe vazio (usa 1330)"
)
async def hp_efetivo_cmd(
    interaction: discord.Interaction,
    vida: app_commands.Range[int, 1, 1000000],
    dr: app_commands.Range[int, 0, 5000],
    evasao: app_commands.Range[int, 0, 5000],
    reducao_dano: app_commands.Range[int, 0, 90] = 30,
    ap_inimigo: app_commands.Range[int, 1, 5000] = 1100,
    precisao_inimigo: app_commands.Range[int, 0, 5000] = 1330
):
    try:
        await interaction.response.defer(ephemeral=True)

        res = hp_efetivo.calcular(vida, dr, evasao, reducao_dano / 100, ap_inimigo, precisao_inimigo)
        imagem = await asyncio.to_thread(hp_efetivo.gerar_grafico, res)

        fmt = hp_efetivo.formatar_numero
        dec = hp_efetivo.formatar_decimal
        status_zona = {
            hp_efetivo.ZONA_BOA: "🟢 Subir **ajuda**",
            hp_efetivo.ZONA_MENOS: "🟡 Subir ainda ajuda, mas **rende menos**",
            hp_efetivo.ZONA_NADA: "⚪ Subir **não ajuda nada** contra esse inimigo",
        }

        embed = discord.Embed(
            title="🛡️ Sua Vida Efetiva",
            description=(
                f"Contra um inimigo com **AP {ap_inimigo}** e **Precisão {precisao_inimigo}**, "
                f"é como se você tivesse **{fmt(res['hp_efetivo'])} de vida**.\n"
                f"Sua vida de verdade é {fmt(vida)}, ou seja, sua defesa faz você aguentar "
                f"**{dec(res['multiplicador'])}x mais** dano."
            ),
            color=discord.Color.orange()
        )
        embed.add_field(
            name="🎯 O inimigo erra em você",
            value=f"**{dec(res['miss'] * 100)}%** dos golpes (golpe errado dá menos dano)",
            inline=False
        )
        embed.add_field(
            name=f"🧱 Sua DR: {dr}",
            value=(
                f"{status_zona[res['zona_dr_atual']]}\n"
                f"+{hp_efetivo.PONTOS_TESTE} de DR = **+{dec(res['ganho_dr'], 2)}%** de vida efetiva"
            ),
            inline=True
        )
        embed.add_field(
            name=f"💨 Sua Evasão: {evasao}",
            value=(
                f"{status_zona[res['zona_evasao_atual']]}\n"
                f"+{hp_efetivo.PONTOS_TESTE} de Evasão = **+{dec(res['ganho_evasao'], 2)}%** de vida efetiva"
            ),
            inline=True
        )
        embed.add_field(
            name="👉 O QUE FAZER",
            value=f"**{hp_efetivo.recomendacao(res)}**",
            inline=False
        )
        embed.add_field(
            name="📖 Como ler o gráfico",
            value=(
                "• A **bolinha laranja** é você.\n"
                "• **Linha mais alta** = você aguenta mais.\n"
                "• Fundo 🟢 verde = subir ajuda | 🟡 amarelo = ajuda menos | ⚪ cinza = não ajuda nada."
            ),
            inline=False
        )
        embed.set_image(url="attachment://hp_efetivo.png")
        embed.set_footer(text=(
            f"Redução de dano usada: {reducao_dano}% | "
            f"Fórmula baseada nos testes do @gpw"
        ))

        await interaction.followup.send(
            embed=embed,
            file=discord.File(imagem, filename="hp_efetivo.png"),
            ephemeral=True
        )

    except Exception as e:
        logger.error(f"Erro ao calcular HP efetivo: {e}")
        if interaction.response.is_done():
            await interaction.followup.send(f"❌ Erro ao calcular HP efetivo: {str(e)}", ephemeral=True)
        else:
            await interaction.response.send_message(f"❌ Erro ao calcular HP efetivo: {str(e)}", ephemeral=True)

@bot.tree.command(name="dano_efetivo", description="Mostra quanto do seu dano REALMENTE chega no inimigo e se vale subir AP ou Precisão")
@app_commands.describe(
    ap="Seu AP, aparece na janela de atributos",
    precisao="Sua Precisão, aparece na janela de atributos",
    dr_inimigo="DR do inimigo. Se não sabe, deixe vazio (usa 850)",
    evasao_inimigo="Evasão do inimigo. Se não sabe, deixe vazio (usa 1250)",
    reducao_dano_inimigo="Redução de dano do inimigo em %. Se não sabe, deixe vazio (usa 30%)"
)
async def dano_efetivo_cmd(
    interaction: discord.Interaction,
    ap: app_commands.Range[int, 1, 5000],
    precisao: app_commands.Range[int, 0, 5000],
    dr_inimigo: app_commands.Range[int, 0, 5000] = 850,
    evasao_inimigo: app_commands.Range[int, 0, 5000] = 1250,
    reducao_dano_inimigo: app_commands.Range[int, 0, 90] = 30
):
    try:
        await interaction.response.defer(ephemeral=True)

        res = hp_efetivo.calcular_dano(ap, precisao, dr_inimigo, evasao_inimigo, reducao_dano_inimigo / 100)
        imagem = await asyncio.to_thread(hp_efetivo.gerar_grafico_dano, res)

        fmt = hp_efetivo.formatar_numero
        dec = hp_efetivo.formatar_decimal

        status_ap = {
            hp_efetivo.ZONA_BOA: "🟢 Subir **ajuda**",
            hp_efetivo.ZONA_MENOS: "🟡 Subir ajuda, mas **rende menos**",
            hp_efetivo.ZONA_NADA: "⚪ Seu AP **quase não passa** a DR dele",
        }
        if res["zona_precisao_atual"] == hp_efetivo.ZONA_BOA:
            status_precisao = "🟢 Subir **ajuda**"
        elif res["precisao_inutil"]:
            status_precisao = "⚪ Subir **não ajuda nada**: seu AP não passa a DR dele"
        elif precisao >= res["precisao_check"]:
            status_precisao = "⚪ Subir **não ajuda nada**: ele já não desvia de você"
        else:
            status_precisao = "⚪ Subir **não ajuda nada** ainda: ele desvia demais"

        embed = discord.Embed(
            title="⚔️ Seu Dano Efetivo",
            description=(
                f"Contra um inimigo com **DR {dr_inimigo}**, **Evasão {evasao_inimigo}** "
                f"e **{reducao_dano_inimigo}% de redução de dano**:\n"
                f"de cada **100** de dano que você bate, só **{dec(res['porcentagem'])}** chegam nele.\n"
                f"Na média, cada golpe seu causa **{fmt(res['dano'])} de dano** (seu AP é {ap})."
            ),
            color=discord.Color.red()
        )
        embed.add_field(
            name="🎯 Ele desvia de você",
            value=f"**{dec(res['miss'] * 100)}%** dos golpes (golpe desviado dá menos dano)",
            inline=False
        )
        embed.add_field(
            name=f"⚔️ Seu AP: {ap}",
            value=(
                f"{status_ap[res['zona_ap_atual']]}\n"
                f"+{hp_efetivo.PONTOS_TESTE} de AP = **+{dec(res['ganho_ap'], 2)}%** de dano\n"
                f"Passa a DR dele a partir de **{res['ap_passa']}**, rende o máximo a partir de **{res['ap_cotovelo']}**"
            ),
            inline=True
        )
        embed.add_field(
            name=f"🎯 Sua Precisão: {precisao}",
            value=(
                f"{status_precisao}\n"
                f"+{hp_efetivo.PONTOS_TESTE} de Precisão = **+{dec(res['ganho_precisao'], 2)}%** de dano\n"
                f"Começa a contar em **{max(res['precisao_inicio'], 0)}**, a partir de **{res['precisao_check']}** ele não desvia mais"
            ),
            inline=True
        )
        embed.add_field(
            name="👉 O QUE FAZER",
            value=f"**{hp_efetivo.recomendacao_dano(res)}**",
            inline=False
        )
        embed.add_field(
            name="📖 Como ler o gráfico",
            value=(
                "• A **bolinha laranja** é você.\n"
                "• **Linha mais alta** = você bate mais.\n"
                "• Fundo 🟢 verde = subir ajuda | 🟡 amarelo = rende menos | ⚪ cinza = quase não ajuda / não ajuda nada."
            ),
            inline=False
        )
        embed.set_image(url="attachment://dano_efetivo.png")
        embed.set_footer(text="Fórmula baseada nos testes do @gpw")

        await interaction.followup.send(
            embed=embed,
            file=discord.File(imagem, filename="dano_efetivo.png"),
            ephemeral=True
        )

    except Exception as e:
        logger.error(f"Erro ao calcular dano efetivo: {e}")
        if interaction.response.is_done():
            await interaction.followup.send(f"❌ Erro ao calcular dano efetivo: {str(e)}", ephemeral=True)
        else:
            await interaction.response.send_message(f"❌ Erro ao calcular dano efetivo: {str(e)}", ephemeral=True)

@bot.tree.command(name="pre", description="[ADMIN] Visualiza o perfil de outro membro")
@app_commands.describe(usuario="Usuário para visualizar o perfil")
async def pre(interaction: discord.Interaction, usuario: discord.Member):
    """Visualiza o perfil de outro membro (apenas administradores)"""
    if not is_admin_user(interaction.user):
        await interaction.response.send_message(
            "❌ Apenas administradores podem usar este comando!",
            ephemeral=True
        )
        return
    
    try:
        # Verificar se é em um servidor
        if not interaction.guild:
            await interaction.response.send_message(
                "❌ Este comando só pode ser usado em um servidor!",
                ephemeral=True
            )
            return
        
        await interaction.response.defer(ephemeral=True)
        
        # Gerar perfil do usuário especificado
        embed = await generate_profile_embed(interaction, usuario)
        
        if embed is None:
            await interaction.followup.send(
                f"❌ Nenhum gearscore encontrado para {usuario.mention}!",
                ephemeral=True
            )
            return
        
        await interaction.followup.send(embed=embed, ephemeral=True)
            
    except Exception as e:
        if interaction.response.is_done():
            await interaction.followup.send(
                f"❌ Erro ao buscar perfil: {str(e)}",
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"❌ Erro ao buscar perfil: {str(e)}",
                ephemeral=True
            )

# ==================== SISTEMA DE ESTATÍSTICAS DE CLASSES ====================

# Modal para enviar DM personalizada
class SendDMModal(discord.ui.Modal, title="📨 Enviar Notificação"):
    def __init__(self, member: discord.Member, family_name: str):
        super().__init__()
        self.target_member = member
        self.family_name = family_name
    
    message = discord.ui.TextInput(
        label="Mensagem",
        style=discord.TextStyle.paragraph,
        placeholder="Digite a mensagem que será enviada para o membro...",
        required=True,
        max_length=1000
    )
    
    async def on_submit(self, interaction: discord.Interaction):
        try:
            dm_embed = discord.Embed(
                title="📨 Notificação da Staff",
                description=self.message.value,
                color=discord.Color.orange(),
                timestamp=discord.utils.utcnow()
            )
            dm_embed.set_footer(text="Staff Mouz")
            
            await self.target_member.send(embed=dm_embed)
            
            await interaction.response.send_message(
                f"✅ Mensagem enviada com sucesso para **{self.family_name}** ({self.target_member.display_name})!",
                ephemeral=True
            )
            logger.info(f"DM enviada para {self.target_member.display_name} (ID: {self.target_member.id}) via estatísticas de classes")
        except discord.Forbidden:
            await interaction.response.send_message(
                f"❌ Não foi possível enviar DM para **{self.family_name}**. O usuário pode ter DMs desabilitadas.",
                ephemeral=True
            )
        except Exception as e:
            await interaction.response.send_message(
                f"❌ Erro ao enviar DM: {str(e)}",
                ephemeral=True
            )


# Modal para DM em massa para toda a classe
class MassDMModal(discord.ui.Modal, title="📢 Notificação em Massa"):
    def __init__(self, class_members: list, guild: discord.Guild, class_name: str):
        super().__init__()
        self.class_members = class_members
        self.guild = guild
        self.class_name = class_name
    
    message = discord.ui.TextInput(
        label="Mensagem para todos da classe",
        style=discord.TextStyle.paragraph,
        placeholder="Esta mensagem será enviada para TODOS os membros desta classe...",
        required=True,
        max_length=1000
    )
    
    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        
        sent = 0
        failed = 0
        
        dm_embed = discord.Embed(
            title=f"📢 Aviso para {self.class_name}s",
            description=self.message.value,
            color=discord.Color.blue(),
            timestamp=discord.utils.utcnow()
        )
        dm_embed.set_footer(text="Staff Mouz")
        
        for family, display, gs, ap, aap, dp, uid, link in self.class_members:
            member = self.guild.get_member(int(uid)) if uid else None
            if member:
                try:
                    await member.send(embed=dm_embed)
                    sent += 1
                except:
                    failed += 1
        
        await interaction.followup.send(
            f"✅ **Notificação em massa enviada!**\n\n"
            f"📤 Enviadas: **{sent}**\n"
            f"❌ Falhas: **{failed}** (DMs bloqueadas)",
            ephemeral=True
        )
        logger.info(f"DM em massa enviada para classe {self.class_name}: {sent} enviadas, {failed} falhas")


# Helper para calcular indicador de GS
def get_gs_indicator(gs: int, avg_gs: float) -> str:
    """Retorna emoji indicador baseado no GS comparado à média"""
    if gs >= avg_gs + 10:
        return "🟢"  # Acima da média (+10)
    elif gs >= avg_gs - 10:
        return "🟡"  # Na média (±10)
    elif gs >= avg_gs - 20:
        return "🟠"  # Pouco abaixo (-10 a -20)
    else:
        return "🔴"  # Muito abaixo (-20 ou mais)


# Helper para criar embed de membros da classe
def create_class_members_embed(class_members: list, selected_class: str, filter_type: str = "all", guild_avg_gs: int = 0):
    """Cria embed formatado com membros da classe"""
    
    # Aplicar filtro
    if filter_type == "no_link":
        filtered = [m for m in class_members if not m[7] or not m[7].startswith('http')]
        filter_text = "🔗 Filtro: Sem Link de Gear"
    elif filter_type == "low_gs":
        avg = sum(m[2] for m in class_members) / len(class_members) if class_members else 0
        filtered = [m for m in class_members if m[2] < avg]
        filter_text = "📉 Filtro: GS Abaixo da Média"
    else:
        filtered = class_members
        filter_text = "📋 Todos os Membros"
    
    # Calcular média para indicadores
    avg_gs = sum(m[2] for m in class_members) / len(class_members) if class_members else 0
    
    embed = discord.Embed(
        title=f"⚔️ {selected_class} — {len(filtered)}/{len(class_members)} membros",
        description=f"**{filter_text}**\n\n"
                    f"🎯 GS Médio da Classe: **{int(avg_gs)}**\n"
                    f"🌐 GS Médio da Guilda: **{guild_avg_gs}**",
        color=discord.Color.blue(),
        timestamp=discord.utils.utcnow()
    )
    
    if filtered:
        members_text = ""
        for i, (family, display, gs, ap, aap, dp, uid, link) in enumerate(filtered, 1):
            # Indicador visual de GS
            indicator = get_gs_indicator(gs, avg_gs)
            
            # Link do gear
            if link and link.startswith('http'):
                gear_link = f"[🔗 Gear]({link})"
            else:
                gear_link = "⚠️ Sem link"
            
            line = f"{indicator} **{family}** • GS: **{gs}** • {gear_link}\n"
            
            if len(members_text) + len(line) > 950:
                embed.add_field(name="📋 Lista", value=members_text, inline=False)
                members_text = line
            else:
                members_text += line
        
        if members_text:
            field_name = "📋 Lista" if len(embed.fields) == 0 else "📋 Continuação"
            embed.add_field(name=field_name, value=members_text, inline=False)
        
        # Estatísticas
        min_gs = min(m[2] for m in filtered)
        max_gs = max(m[2] for m in filtered)
        with_link = sum(1 for m in filtered if m[7] and m[7].startswith('http'))
        without_link = len(filtered) - with_link
        
        embed.add_field(
            name="📊 Estatísticas",
            value=f"**Menor:** {min_gs} │ **Maior:** {max_gs}\n"
                  f"**🔗 Com Link:** {with_link} │ **⚠️ Sem Link:** {without_link}",
            inline=False
        )
        
        # Legenda dos indicadores
        embed.add_field(
            name="🚦 Legenda",
            value="🟢 Acima (+10) │ 🟡 Na média (±10) │ 🟠 Abaixo (-10 a -20) │ 🔴 Muito abaixo (-20+)",
            inline=False
        )
    else:
        embed.add_field(name="📋 Lista", value="*Nenhum membro encontrado com este filtro*", inline=False)
    
    return embed


# Select para escolher membro e enviar DM
class MemberDMSelect(discord.ui.Select):
    def __init__(self, class_members: list, guild: discord.Guild):
        self.class_members = class_members
        self.guild = guild
        
        options = []
        for i, (family, display, gs, ap, aap, dp, uid, link) in enumerate(class_members[:25]):
            has_link = "🔗" if link and link.startswith('http') else "⚠️"
            options.append(
                discord.SelectOption(
                    label=f"{family}",
                    description=f"GS: {gs} │ {display} │ {has_link}",
                    value=str(uid),
                    emoji="📨"
                )
            )
        
        super().__init__(
            placeholder="📨 Enviar DM individual...",
            options=options,
            min_values=1,
            max_values=1,
            row=1
        )
    
    async def callback(self, interaction: discord.Interaction):
        user_id = self.values[0]
        member = self.guild.get_member(int(user_id))
        
        if not member:
            await interaction.response.send_message("❌ Membro não encontrado!", ephemeral=True)
            return
        
        family_name = next((f for f, d, g, a, aa, dp, u, l in self.class_members if str(u) == user_id), "Membro")
        modal = SendDMModal(member, family_name)
        await interaction.response.send_modal(modal)


# Select de filtros
class FilterSelect(discord.ui.Select):
    def __init__(self, parent_view):
        self.parent_view = parent_view
        
        options = [
            discord.SelectOption(label="Todos os Membros", value="all", emoji="📋", description="Mostrar todos"),
            discord.SelectOption(label="Sem Link de Gear", value="no_link", emoji="⚠️", description="Membros que precisam adicionar link"),
            discord.SelectOption(label="GS Abaixo da Média", value="low_gs", emoji="📉", description="Membros com GS menor que a média"),
        ]
        
        super().__init__(
            placeholder="🔍 Filtrar membros...",
            options=options,
            min_values=1,
            max_values=1,
            row=2
        )
    
    async def callback(self, interaction: discord.Interaction):
        filter_type = self.values[0]
        self.parent_view.current_filter = filter_type
        
        embed = create_class_members_embed(
            self.parent_view.current_class_members,
            self.parent_view.current_class,
            filter_type,
            self.parent_view.guild_avg_gs
        )
        
        await interaction.response.edit_message(embed=embed, view=self.parent_view)


# Botões de ação rápida
class QuickActionButtons(discord.ui.View):
    pass  # Placeholder


class MassDMButton(discord.ui.Button):
    def __init__(self, parent_view):
        super().__init__(
            style=discord.ButtonStyle.primary,
            label="📢 DM em Massa",
            custom_id="mass_dm",
            row=3
        )
        self.parent_view = parent_view
    
    async def callback(self, interaction: discord.Interaction):
        if not self.parent_view.current_class_members:
            await interaction.response.send_message("❌ Nenhum membro na lista!", ephemeral=True)
            return
        
        modal = MassDMModal(
            self.parent_view.current_class_members,
            self.parent_view.guild,
            self.parent_view.current_class
        )
        await interaction.response.send_modal(modal)


class RequestUpdateButton(discord.ui.Button):
    def __init__(self, parent_view):
        super().__init__(
            style=discord.ButtonStyle.secondary,
            label="🔄 Pedir Atualização",
            custom_id="request_update",
            row=3
        )
        self.parent_view = parent_view
    
    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        
        sent = 0
        failed = 0
        
        dm_embed = discord.Embed(
            title="🔄 Solicitação de Atualização",
            description=f"Olá! A Staff da **Mouz** está solicitando que você atualize seu gearscore.\n\n"
                        f"Por favor, use o comando `/atualizar` para manter seus dados em dia.\n\n"
                        f"*Mesmo que não tenha evoluído, atualize para controle interno da guilda.*",
            color=discord.Color.orange(),
            timestamp=discord.utils.utcnow()
        )
        dm_embed.set_footer(text="Staff Mouz")
        
        for family, display, gs, ap, aap, dp, uid, link in self.parent_view.current_class_members:
            member = self.parent_view.guild.get_member(int(uid)) if uid else None
            if member:
                try:
                    await member.send(embed=dm_embed)
                    sent += 1
                except:
                    failed += 1
        
        await interaction.followup.send(
            f"✅ **Solicitação de atualização enviada!**\n"
            f"📤 Enviadas: **{sent}** │ ❌ Falhas: **{failed}**",
            ephemeral=True
        )


class RequestLinkButton(discord.ui.Button):
    def __init__(self, parent_view):
        super().__init__(
            style=discord.ButtonStyle.secondary,
            label="🔗 Pedir Link",
            custom_id="request_link",
            row=3
        )
        self.parent_view = parent_view
    
    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        
        # Filtrar apenas quem não tem link
        no_link_members = [m for m in self.parent_view.current_class_members if not m[7] or not m[7].startswith('http')]
        
        if not no_link_members:
            await interaction.followup.send("✅ Todos os membros desta classe já têm link de gear!", ephemeral=True)
            return
        
        sent = 0
        failed = 0
        
        dm_embed = discord.Embed(
            title="🔗 Solicitação de Link de Gear",
            description=f"Olá! Notamos que você ainda não adicionou o **link do seu gear** no registro.\n\n"
                        f"Por favor, use o comando `/atualizar` e inclua o link do seu gear (bdoplanner ou similar).\n\n"
                        f"*O link ajuda a staff a visualizar seu equipamento completo.*",
            color=discord.Color.orange(),
            timestamp=discord.utils.utcnow()
        )
        dm_embed.set_footer(text="Staff Mouz")
        
        for family, display, gs, ap, aap, dp, uid, link in no_link_members:
            member = self.parent_view.guild.get_member(int(uid)) if uid else None
            if member:
                try:
                    await member.send(embed=dm_embed)
                    sent += 1
                except:
                    failed += 1
        
        await interaction.followup.send(
            f"✅ **Solicitação de link enviada!**\n"
            f"📤 Enviadas: **{sent}** │ ❌ Falhas: **{failed}**",
            ephemeral=True
        )


class ExportListButton(discord.ui.Button):
    def __init__(self, parent_view):
        super().__init__(
            style=discord.ButtonStyle.secondary,
            label="📋 Exportar Lista",
            custom_id="export_list",
            row=4
        )
        self.parent_view = parent_view
    
    async def callback(self, interaction: discord.Interaction):
        if not self.parent_view.current_class_members:
            await interaction.response.send_message("❌ Nenhum membro na lista!", ephemeral=True)
            return
        
        # Criar lista formatada
        export_text = f"📋 **{self.parent_view.current_class}** - {len(self.parent_view.current_class_members)} membros\n"
        export_text += "```\n"
        export_text += f"{'#':<3} {'Família':<20} {'GS':<6} {'AP':<4} {'AAP':<4} {'DP':<4} {'Link':<5}\n"
        export_text += "-" * 50 + "\n"
        
        for i, (family, display, gs, ap, aap, dp, uid, link) in enumerate(self.parent_view.current_class_members, 1):
            has_link = "Sim" if link and link.startswith('http') else "Não"
            family_short = family[:18] + ".." if len(family) > 20 else family
            export_text += f"{i:<3} {family_short:<20} {gs:<6} {ap:<4} {aap:<4} {dp:<4} {has_link:<5}\n"
        
        export_text += "```"
        
        # Se for muito longo, enviar em partes
        if len(export_text) > 2000:
            export_text = export_text[:1990] + "...\n```"
        
        await interaction.response.send_message(export_text, ephemeral=True)


class ClassStatsBackButton(discord.ui.Button):
    def __init__(self, parent_view):
        super().__init__(
            style=discord.ButtonStyle.danger,
            label="◀️ Voltar",
            custom_id="back_to_stats",
            row=4
        )
        self.parent_view = parent_view
    
    async def callback(self, interaction: discord.Interaction):
        self.parent_view.reset_to_original()
        await interaction.response.edit_message(embed=self.parent_view.original_embed, view=self.parent_view)


# View interativa para estatísticas de classes
class ClassStatsSelect(discord.ui.Select):
    def __init__(self, stats_data: list, guild: discord.Guild, valid_user_ids: list, parent_view):
        self.stats_data = stats_data
        self.guild = guild
        self.valid_user_ids = valid_user_ids
        self.parent_view = parent_view
        
        options = []
        for class_name, total, avg_gs in stats_data[:25]:
            avg_gs_int = int(round(avg_gs)) if avg_gs else 0
            options.append(
                discord.SelectOption(
                    label=class_name,
                    description=f"{total} membro(s) • GS Médio: {avg_gs_int}",
                    value=class_name,
                    emoji="⚔️"
                )
            )
        
        super().__init__(
            placeholder="📋 Selecione uma classe...",
            options=options,
            min_values=1,
            max_values=1,
            row=0
        )
    
    async def callback(self, interaction: discord.Interaction):
        selected_class = self.values[0]
        
        # DEBUG: Log para verificar o que está acontecendo
        logger.info(f"[DEBUG] Classe selecionada: '{selected_class}'")
        logger.info(f"[DEBUG] valid_user_ids count: {len(self.valid_user_ids) if self.valid_user_ids else 0}")
        
        # Buscar membros da classe
        all_gearscores = db.get_all_gearscores(valid_user_ids=self.valid_user_ids)
        
        # DEBUG: Log dos dados retornados
        logger.info(f"[DEBUG] Total de registros retornados: {len(all_gearscores) if all_gearscores else 0}")
        if all_gearscores and len(all_gearscores) > 0:
            sample = all_gearscores[0]
            logger.info(f"[DEBUG] Tipo do registro: {type(sample)}")
            logger.info(f"[DEBUG] Tamanho do registro: {len(sample) if hasattr(sample, '__len__') else 'N/A'}")
            if not isinstance(sample, dict):
                logger.info(f"[DEBUG] Registro completo: {sample}")
        
        class_members = []
        classes_found = set()  # DEBUG: Para coletar todas as classes encontradas
        for record in all_gearscores:
            if isinstance(record, dict):
                class_pvp = record.get('class_pvp', '')
                user_id = record.get('user_id', '')
                family_name = record.get('family_name', 'N/A')
                ap = record.get('ap', 0)
                aap = record.get('aap', 0)
                dp = record.get('dp', 0)
                linkgear = record.get('linkgear', '')
            else:
                class_pvp = record[4] if len(record) > 4 else ''
                user_id = record[1] if len(record) > 1 else ''
                family_name = record[2] if len(record) > 2 else 'N/A'
                ap = record[5] if len(record) > 5 else 0
                aap = record[6] if len(record) > 6 else 0
                dp = record[7] if len(record) > 7 else 0
                linkgear = record[8] if len(record) > 8 else ''
            
            classes_found.add(str(class_pvp))  # DEBUG
            
            # Comparação case-insensitive e com strip para evitar problemas
            if str(class_pvp).strip().lower() == str(selected_class).strip().lower():
                gs_total = max(int(ap or 0), int(aap or 0)) + int(dp or 0)
                member = self.guild.get_member(int(user_id)) if user_id else None
                display_name = member.display_name if member else "Desconhecido"
                class_members.append((family_name, display_name, gs_total, ap, aap, dp, user_id, linkgear))
        
        # DEBUG: Log das classes encontradas
        logger.info(f"[DEBUG] Classes encontradas nos dados: {classes_found}")
        logger.info(f"[DEBUG] Total de membros encontrados para '{selected_class}': {len(class_members)}")
        
        # Ordenar por GS
        class_members.sort(key=lambda x: x[2], reverse=True)
        
        # Salvar na view
        self.parent_view.current_class_members = class_members
        self.parent_view.current_class = selected_class
        self.parent_view.current_filter = "all"
        
        # Criar embed
        embed = create_class_members_embed(class_members, selected_class, "all", self.parent_view.guild_avg_gs)
        
        # Atualizar view
        self.parent_view.update_for_class_view(class_members)
        
        await interaction.response.edit_message(embed=embed, view=self.parent_view)


class ClassStatsView(discord.ui.View):
    def __init__(self, stats_data: list, guild: discord.Guild, valid_user_ids: list, original_embed: discord.Embed, guild_avg_gs: int = 0):
        super().__init__(timeout=600)  # 10 minutos de timeout
        self.stats_data = stats_data
        self.guild = guild
        self.valid_user_ids = valid_user_ids
        self.original_embed = original_embed
        self.guild_avg_gs = guild_avg_gs
        self.current_class_members = []
        self.current_class = ""
        self.current_filter = "all"
        
        # Select de classes
        self.class_select = ClassStatsSelect(stats_data, guild, valid_user_ids, self)
        self.add_item(self.class_select)
        
        # Componentes dinâmicos
        self.dm_select = None
        self.filter_select = None
        self.mass_dm_btn = None
        self.request_update_btn = None
        self.request_link_btn = None
        self.export_btn = None
        self.back_button = None
    
    def update_for_class_view(self, class_members: list):
        """Adiciona todos os componentes quando uma classe é selecionada"""
        # Limpar componentes antigos
        self._clear_dynamic_components()
        
        if class_members:
            # Select de DM individual
            self.dm_select = MemberDMSelect(class_members, self.guild)
            self.add_item(self.dm_select)
            
            # Select de filtros
            self.filter_select = FilterSelect(self)
            self.add_item(self.filter_select)
            
            # Botões de ação
            self.mass_dm_btn = MassDMButton(self)
            self.add_item(self.mass_dm_btn)
            
            self.request_update_btn = RequestUpdateButton(self)
            self.add_item(self.request_update_btn)
            
            self.request_link_btn = RequestLinkButton(self)
            self.add_item(self.request_link_btn)
            
            self.export_btn = ExportListButton(self)
            self.add_item(self.export_btn)
        
        # Botão voltar sempre
        self.back_button = ClassStatsBackButton(self)
        self.add_item(self.back_button)
    
    def _clear_dynamic_components(self):
        """Remove componentes dinâmicos"""
        for component in [self.dm_select, self.filter_select, self.mass_dm_btn, 
                         self.request_update_btn, self.request_link_btn, 
                         self.export_btn, self.back_button]:
            if component and component in self.children:
                self.remove_item(component)
    
    def reset_to_original(self):
        """Reseta para o estado original"""
        self._clear_dynamic_components()
        self.dm_select = None
        self.filter_select = None
        self.mass_dm_btn = None
        self.request_update_btn = None
        self.request_link_btn = None
        self.export_btn = None
        self.back_button = None
        self.current_class_members = []
        self.current_class = ""
        self.current_filter = "all"


@bot.tree.command(name="estatisticas_classes", description="[ADMIN] Mostra estatísticas das classes na guilda")
async def estatisticas_classes(interaction: discord.Interaction):
    """Mostra estatísticas das classes na guilda (apenas administradores)"""
    if not is_admin_user(interaction.user):
        await interaction.response.send_message(
            "❌ Apenas administradores podem usar este comando!",
            ephemeral=True
        )
        return
    
    # Defer para evitar timeout (o comando pode demorar)
    await interaction.response.defer(ephemeral=True)
    
    try:
        # Buscar apenas membros que têm o cargo da guilda
        valid_user_ids = await get_guild_member_ids(interaction.guild)
        
        if not valid_user_ids:
            await interaction.followup.send(
                "❌ Nenhum membro com o cargo da guilda encontrado!",
                ephemeral=True
            )
            return
        
        stats = db.get_class_statistics(valid_user_ids=valid_user_ids)
        
        if not stats:
            await interaction.followup.send(
                "❌ Nenhum gearscore cadastrado ainda!",
                ephemeral=True
            )
            return
        
        # Calcular GS médio geral
        total_chars = 0
        total_weighted_gs = 0
        total_chars_sem_shai = 0
        total_weighted_gs_sem_shai = 0
        stats_list = []
        
        for stat in stats:
            # Formatar dados dependendo do banco
            if isinstance(stat, dict):
                class_name = stat.get('class_pvp', 'Desconhecida')
                total = stat.get('total', 0)
                avg_gs = stat.get('avg_gs', 0)
            else:
                class_name = stat[0]
                total = stat[1]
                avg_gs = float(stat[2]) if len(stat) > 2 and stat[2] is not None else 0
            
            total_chars += total
            total_weighted_gs += avg_gs * total
            
            # Calcular GS médio sem Shai
            if class_name.lower() != 'shai':
                total_chars_sem_shai += total
                total_weighted_gs_sem_shai += avg_gs * total
            
            stats_list.append((class_name, total, avg_gs))
        
        # ✅ ORDENAR por quantidade (maior para menor)
        stats_list.sort(key=lambda x: x[1], reverse=True)
        
        # Calcular GS médio geral (média ponderada)
        overall_avg_gs = int(round(total_weighted_gs / total_chars)) if total_chars > 0 else 0
        
        # Calcular GS médio sem Shai (média ponderada)
        overall_avg_gs_sem_shai = int(round(total_weighted_gs_sem_shai / total_chars_sem_shai)) if total_chars_sem_shai > 0 else 0
        
        embed = discord.Embed(
            title="🎭 Estatísticas das Classes - Guilda",
            description="📊 Distribuição e GS médio por classe\n\n*Selecione uma classe no menu abaixo para ver os membros*",
            color=discord.Color.purple(),
            timestamp=discord.utils.utcnow()
        )
        
        # Adicionar GS médio geral e sem Shai lado a lado
        embed.add_field(
            name="📊 GS Médio Geral",
            value=f"**{overall_avg_gs}**",
            inline=True
        )
        
        embed.add_field(
            name="📊 GS Médio (Sem Shai)",
            value=f"**{overall_avg_gs_sem_shai}**",
            inline=True
        )
        
        embed.add_field(name="\u200b", value="\u200b", inline=True)  # Espaçador
        
        # Criar lista formatada das classes (ordenada por quantidade)
        # Dividir em múltiplos campos se necessário (limite de 1024 caracteres por field)
        class_ranking_parts = []
        current_part = ""
        field_count = 0
        
        for i, (class_name, total, avg_gs) in enumerate(stats_list, 1):
            avg_gs_int = int(round(avg_gs)) if avg_gs else 0
            # Emoji baseado na posição
            if i == 1:
                medal = "🥇"
            elif i == 2:
                medal = "🥈"
            elif i == 3:
                medal = "🥉"
            else:
                medal = f"`{i:2d}`"
            
            line = f"{medal} **{class_name}** — {total} membro(s) • GS: {avg_gs_int}\n"
            
            # Truncar linha se for muito grande (não deve acontecer, mas por segurança)
            if len(line) > 1024:
                line = line[:1020] + "...\n"
            
            # Verificar se adicionar esta linha excederia o limite
            if len(current_part) + len(line) > 1024:
                # Se exceder, salvar o campo atual e começar um novo
                if current_part:
                    class_ranking_parts.append(current_part)
                current_part = line  # Começar novo campo com a linha atual
            else:
                current_part += line
        
        # Adicionar o último campo se houver conteúdo
        if current_part:
            class_ranking_parts.append(current_part)
        
        # Adicionar os campos ao embed
        if class_ranking_parts:
            for idx, part in enumerate(class_ranking_parts):
                field_count += 1
                field_name = "🏆 Ranking de Classes (por quantidade)" if field_count == 1 else f"🏆 Ranking (cont. {field_count})"
                # Garantir que não exceda 1024 (por segurança)
                value = part[:1024] if len(part) > 1024 else part
                embed.add_field(
                    name=field_name,
                    value=value,
                    inline=False
                )
        elif not stats_list:
            embed.add_field(
                name="🏆 Ranking de Classes",
                value="Nenhuma classe encontrada",
                inline=False
            )
        
        embed.set_footer(text=f"Total de {total_chars} personagens cadastrados • Selecione uma classe abaixo")
        
        # Criar a View com o menu interativo
        view = ClassStatsView(stats_list, interaction.guild, valid_user_ids, embed, overall_avg_gs)
        
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        
    except Exception as e:
        logger.error(f"Erro ao buscar estatísticas de classes: {e}")
        if interaction.response.is_done():
            await interaction.followup.send(
                f"❌ Erro ao buscar estatísticas: {str(e)}",
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"❌ Erro ao buscar estatísticas: {str(e)}",
                ephemeral=True
            )

@bot.tree.command(name="stats", description="[ADMIN] Mostra estatísticas completas de todos os membros")
async def stats(interaction: discord.Interaction):
    """Mostra lista completa de todos os membros com gearscore (apenas administradores)"""
    if not is_admin_user(interaction.user):
        await interaction.response.send_message(
            "❌ Apenas administradores podem usar este comando!",
            ephemeral=True
        )
        return
    
    try:
        # Buscar apenas membros que têm o cargo da guilda
        if not interaction.guild:
            await interaction.response.send_message(
                "❌ Este comando só pode ser usado em um servidor!",
                ephemeral=True
            )
            return
        
        await interaction.response.defer(ephemeral=True)  # Só o admin que executou vê
        
        valid_user_ids = await get_guild_member_ids(interaction.guild)
        results = db.get_all_gearscores(valid_user_ids=valid_user_ids)
        
        if not results:
            await interaction.followup.send(
                "❌ Nenhum gearscore cadastrado ainda!",
                ephemeral=True
            )
            return
        
        # Ordenar por gearscore total (MAX(AP, AAP) + DP) - do maior para o menor
        def get_gs_from_result(result):
            if isinstance(result, dict):
                # MongoDB retorna como dict
                ap = result.get('ap', 0)
                aap = result.get('aap', 0)
                dp = result.get('dp', 0)
            else:
                # SQLite/PostgreSQL: SELECT retorna tupla
                # Ordem com character_name: id(0), user_id(1), family_name(2), character_name(3), class_pvp(4), ap(5), aap(6), dp(7), linkgear(8), updated_at(9)
                # Ordem sem character_name: id(0), user_id(1), family_name(2), class_pvp(3), ap(4), aap(5), dp(6), linkgear(7), updated_at(8)
                if len(result) >= 10:
                    # Tem character_name
                    ap = result[5] if len(result) > 5 else 0
                    aap = result[6] if len(result) > 6 else 0
                    dp = result[7] if len(result) > 7 else 0
                else:
                    # Não tem character_name (PostgreSQL antigo)
                    ap = result[4] if len(result) > 4 else 0
                    aap = result[5] if len(result) > 5 else 0
                    dp = result[6] if len(result) > 6 else 0
            
            # Garantir que são inteiros
            try:
                ap = int(ap) if ap is not None else 0
                aap = int(aap) if aap is not None else 0
                dp = int(dp) if dp is not None else 0
            except (ValueError, TypeError):
                ap = 0
                aap = 0
                dp = 0
            
            return calculate_gs(ap, aap, dp)
        
        # Ordenar por GS (do maior para o menor)
        sorted_results = sorted(results, key=get_gs_from_result, reverse=True)
        
        # Dividir em partes de 30 membros cada (similar às imagens)
        members_per_page = 30
        total_pages = (len(sorted_results) + members_per_page - 1) // members_per_page
        
        for page in range(total_pages):
            start_idx = page * members_per_page
            end_idx = min(start_idx + members_per_page, len(sorted_results))
            page_results = sorted_results[start_idx:end_idx]
            
            embed = discord.Embed(
                title=f"Membros (Geral) - Parte {page + 1}",
                color=discord.Color.blue(),
                timestamp=discord.utils.utcnow()
            )
            
            # Criar lista de membros
            members_list = []
            for i, result in enumerate(page_results, start=start_idx + 1):
                # Formatar dados dependendo do banco
                if isinstance(result, dict):
                    # MongoDB retorna como dict
                    family_name = result.get('family_name', 'N/A')
                    character_name = result.get('character_name', None)
                    class_pvp = result.get('class_pvp', 'N/A')
                    ap = result.get('ap', 0)
                    aap = result.get('aap', 0)
                    dp = result.get('dp', 0)
                    linkgear = result.get('linkgear', 'N/A')
                else:
                    # SQLite/PostgreSQL: SELECT retorna tupla
                    # Ordem: id(0), user_id(1), family_name(2), character_name(3), class_pvp(4), ap(5), aap(6), dp(7), linkgear(8), updated_at(9)
                    # Mas PostgreSQL pode não ter character_name no SELECT, então verificar tamanho
                    if len(result) >= 10:
                        # Tem character_name
                        family_name = result[2] if len(result) > 2 else 'N/A'
                        character_name = result[3] if len(result) > 3 else None
                        class_pvp = result[4] if len(result) > 4 else 'N/A'
                        ap = result[5] if len(result) > 5 else 0
                        aap = result[6] if len(result) > 6 else 0
                        dp = result[7] if len(result) > 7 else 0
                        linkgear_raw = result[8] if len(result) > 8 else 'N/A'
                    else:
                        # Não tem character_name (PostgreSQL antigo)
                        family_name = result[2] if len(result) > 2 else 'N/A'
                        character_name = None
                        class_pvp = result[3] if len(result) > 3 else 'N/A'
                        ap = result[4] if len(result) > 4 else 0
                        aap = result[5] if len(result) > 5 else 0
                        dp = result[6] if len(result) > 6 else 0
                        linkgear_raw = result[7] if len(result) > 7 else 'N/A'
                    
                    # Se for datetime, significa que pegamos o campo errado, usar N/A
                    if isinstance(linkgear_raw, datetime):
                        linkgear = 'N/A'
                    else:
                        linkgear = linkgear_raw
                
                # Se character_name não foi definido, usar family_name
                if character_name is None:
                    character_name = family_name
                
                # Garantir que ap, aap, dp são inteiros
                try:
                    ap = int(ap) if ap is not None else 0
                    aap = int(aap) if aap is not None else 0
                    dp = int(dp) if dp is not None else 0
                except (ValueError, TypeError):
                    ap = 0
                    aap = 0
                    dp = 0
                
                gearscore_total = calculate_gs(ap, aap, dp)
                
                # Formatar link gear - garantir que é string e não datetime
                if linkgear is None:
                    linkgear_str = 'N/A'
                elif isinstance(linkgear, datetime):
                    # Se for datetime, significa que pegamos o campo errado, usar N/A
                    linkgear_str = 'N/A'
                else:
                    linkgear_str = str(linkgear)
                
                if linkgear_str and linkgear_str != 'N/A' and linkgear_str != 'None' and linkgear_str.strip():
                    # Verificar se é uma string válida antes de usar startswith
                    linkgear_clean = linkgear_str.strip()
                    if isinstance(linkgear_clean, str) and (linkgear_clean.startswith('http://') or linkgear_clean.startswith('https://')):
                        # Link válido - criar markdown link do Discord
                        link_text = f"([Link Gear]({linkgear_clean}))"
                    elif isinstance(linkgear_clean, str) and linkgear_clean.strip():
                        # Texto mas não é URL - tentar criar link mesmo assim (Discord pode não funcionar, mas mostra o texto)
                        # Se não começa com http, adicionar https://
                        if not linkgear_clean.startswith('http'):
                            link_text = f"([Link Gear](https://{linkgear_clean}))"
                        else:
                            link_text = f"([Link Gear]({linkgear_clean}))"
                    else:
                        link_text = "(Link Gear)"
                else:
                    link_text = "(Link Gear)"
                
                # Formato: "1. Nome (Classe) - 861gs - (Link Gear)"
                # Usar character_name se disponível, senão usar family_name
                # Garantir que não está None ou vazio
                if character_name and str(character_name).strip() and str(character_name) != 'N/A' and str(character_name) != 'None':
                    display_name = str(character_name).strip()
                elif family_name and str(family_name).strip() and str(family_name) != 'N/A':
                    display_name = str(family_name).strip()
                else:
                    display_name = 'N/A'
                
                # Garantir que class_pvp não está vazio e é string
                class_pvp_str = str(class_pvp).strip() if class_pvp and str(class_pvp) != 'N/A' else 'Desconhecida'
                
                # Debug: verificar se os valores estão corretos
                # Se display_name parece ser um número, pode estar invertido
                if display_name.isdigit() and class_pvp_str and not class_pvp_str.isdigit():
                    # Parece estar invertido, trocar
                    temp = display_name
                    display_name = class_pvp_str
                    class_pvp_str = temp
                
                member_line = f"{i}. {display_name} ({class_pvp_str}) - {gearscore_total}gs - {link_text}"
                members_list.append(member_line)
            
            # Adicionar como campo de descrição (pode ter até 4096 caracteres)
            description_text = "\n".join(members_list)
            
            # Se exceder o limite, dividir em chunks
            max_length = 4096
            if len(description_text) <= max_length:
                embed.description = description_text
            else:
                # Dividir em múltiplos campos se necessário
                current_chunk = []
                current_length = 0
                chunk_num = 1
                
                for member_line in members_list:
                    line_length = len(member_line) + 1  # +1 para o \n
                    if current_length + line_length > 1024:  # Limite por field
                        embed.add_field(
                            name=f"Lista {chunk_num}",
                            value="\n".join(current_chunk),
                            inline=False
                        )
                        current_chunk = [member_line]
                        current_length = line_length
                        chunk_num += 1
                    else:
                        current_chunk.append(member_line)
                        current_length += line_length
                
                if current_chunk:
                    embed.add_field(
                        name=f"Lista {chunk_num}",
                        value="\n".join(current_chunk),
                        inline=False
                    )
            
            embed.set_footer(text=f"Total: {len(sorted_results)} membros | Página {page + 1}/{total_pages}")
            
            await interaction.followup.send(embed=embed, ephemeral=True)
        
    except Exception as e:
        if interaction.response.is_done():
            await interaction.followup.send(
                f"❌ Erro ao buscar estatísticas: {str(e)}",
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"❌ Erro ao buscar estatísticas: {str(e)}",
                ephemeral=True
            )

@bot.tree.command(name="ranking_gearscore", description="[ADMIN] Mostra o ranking de gearscore")
async def ranking_gearscore(interaction: discord.Interaction):
    """Mostra o ranking de gearscore (apenas administradores)"""
    if not is_admin_user(interaction.user):
        await interaction.response.send_message(
            "❌ Apenas administradores podem usar este comando!",
            ephemeral=True
        )
        return
    
    try:
        # Buscar apenas membros que têm o cargo da guilda
        if not interaction.guild:
            await interaction.response.send_message(
                "❌ Este comando só pode ser usado em um servidor!",
                ephemeral=True
            )
            return
        
        await interaction.response.defer(ephemeral=True)
        
        valid_user_ids = await get_guild_member_ids(interaction.guild)
        results = db.get_all_gearscores(valid_user_ids=valid_user_ids)
        
        if not results:
            await interaction.followup.send(
                "❌ Nenhum gearscore cadastrado ainda!",
                ephemeral=True
            )
            return
        
        # Ordenar por gearscore total (MAX(AP, AAP) + DP)
        # Formatar dados dependendo do banco
        def get_gs_from_result(result):
            if isinstance(result, dict):
                ap = result.get('ap', 0)
                aap = result.get('aap', 0)
                dp = result.get('dp', 0)
            else:
                # Ordem das colunas: id(0), user_id(1), family_name(2), character_name(3), class_pvp(4), ap(5), aap(6), dp(7), linkgear(8), updated_at(9)
                ap = result[5] if len(result) > 5 else 0
                aap = result[6] if len(result) > 6 else 0
                dp = result[7] if len(result) > 7 else 0
            return calculate_gs(ap, aap, dp)
        
        sorted_results = sorted(results, key=get_gs_from_result, reverse=True)
        
        embed = discord.Embed(
            title="🏆 Ranking de Gearscore",
            color=discord.Color.gold(),
            timestamp=discord.utils.utcnow()
        )
        
        for i, result in enumerate(sorted_results[:10], 1):  # Top 10
            # Formatar dados dependendo do banco
            if isinstance(result, dict):
                family_name = result.get('family_name', 'N/A')
                class_pvp = result.get('class_pvp', 'N/A')
                ap = result.get('ap', 0)
                aap = result.get('aap', 0)
                dp = result.get('dp', 0)
            else:
                # Ordem das colunas: id(0), user_id(1), family_name(2), character_name(3), class_pvp(4), ap(5), aap(6), dp(7), linkgear(8), updated_at(9)
                family_name = result[2] if len(result) > 2 else 'N/A'
                class_pvp = result[4] if len(result) > 4 else 'N/A'
                ap = result[5] if len(result) > 5 else 0
                aap = result[6] if len(result) > 6 else 0
                dp = result[7] if len(result) > 7 else 0
            
            gearscore_total = calculate_gs(ap, aap, dp)
            info = f"**{family_name}**\n"
            info += f"Classe: {class_pvp}\n"
            info += f"AP: {ap} | AAP: {aap} | DP: {dp}\n"
            info += f"**Total: {gearscore_total}**"
            
            medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"#{i}"
            embed.add_field(name=f"{medal} {family_name}", value=info, inline=False)
        
        await interaction.followup.send(embed=embed, ephemeral=True)
        
    except Exception as e:
        if interaction.response.is_done():
            await interaction.followup.send(
                f"❌ Erro ao buscar ranking: {str(e)}",
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"❌ Erro ao buscar ranking: {str(e)}",
                ephemeral=True
            )

@bot.tree.command(name="membros_classe", description="[ADMIN] Visualiza todos os membros registrados de uma classe")
@app_commands.describe(
    classe="Classe a ser visualizada (digite para buscar)"
)
@app_commands.autocomplete(classe=classe_autocomplete)
async def membros_classe(interaction: discord.Interaction, classe: str):
    """Visualiza todos os membros registrados de uma classe com todas as informações (apenas administradores)"""
    if not is_admin_user(interaction.user):
        await interaction.response.send_message(
            "❌ Apenas administradores podem usar este comando!",
            ephemeral=True
        )
        return
    
    # Validar classe
    if classe not in BDO_CLASSES:
        await interaction.response.send_message(
            f"❌ Classe inválida! Use o autocomplete para selecionar uma classe válida.",
            ephemeral=True
        )
        return
    
    try:
        # Verificar se é em um servidor
        if not interaction.guild:
            await interaction.response.send_message(
                "❌ Este comando só pode ser usado em um servidor!",
                ephemeral=True
            )
            return
        
        await interaction.response.defer(ephemeral=True)
        
        # Buscar apenas membros que têm o cargo da guilda
        valid_user_ids = await get_guild_member_ids(interaction.guild)
        members = db.get_class_members(classe, valid_user_ids=valid_user_ids)
        
        if not members:
            await interaction.followup.send(
                f"❌ Nenhum membro encontrado com a classe **{classe}** (apenas membros com cargo da guilda)",
                ephemeral=True
            )
            return
        
        # Ordenar por GS (maior para menor)
        def get_gs_from_member(member):
            if isinstance(member, dict):
                ap = member.get('ap', 0)
                aap = member.get('aap', 0)
                dp = member.get('dp', 0)
            else:
                # Ordem das colunas: id(0), user_id(1), family_name(2), character_name(3), class_pvp(4), ap(5), aap(6), dp(7), linkgear(8), updated_at(9)
                ap = member[5] if len(member) > 5 else 0
                aap = member[6] if len(member) > 6 else 0
                dp = member[7] if len(member) > 7 else 0
            return calculate_gs(ap, aap, dp)
        
        sorted_members = sorted(members, key=get_gs_from_member, reverse=True)
        
        # Criar embed principal
        embed = discord.Embed(
            title=f"🎭 {classe} - Membros Registrados",
            description=f"Total: **{len(sorted_members)}** membro(s) registrado(s)",
            color=discord.Color.blue(),
            timestamp=discord.utils.utcnow()
        )
        
        # Adicionar informações de cada membro
        for i, member in enumerate(sorted_members, 1):
            # Formatar dados dependendo do banco
            if isinstance(member, dict):
                family_name = member.get('family_name', 'N/A')
                ap = member.get('ap', 0)
                aap = member.get('aap', 0)
                dp = member.get('dp', 0)
                linkgear = member.get('linkgear', 'N/A')
                updated_at = member.get('updated_at', 'N/A')
            else:
                # Ordem das colunas: id(0), user_id(1), family_name(2), character_name(3), class_pvp(4), ap(5), aap(6), dp(7), linkgear(8), updated_at(9)
                family_name = member[2] if len(member) > 2 else 'N/A'
                ap = member[5] if len(member) > 5 else 0
                aap = member[6] if len(member) > 6 else 0
                dp = member[7] if len(member) > 7 else 0
                linkgear = member[8] if len(member) > 8 else 'N/A'
                updated_at = member[9] if len(member) > 9 else 'N/A'
            
            gs_total = calculate_gs(ap, aap, dp)
            
            # Formatar data de atualização
            if updated_at and updated_at != 'N/A':
                if hasattr(updated_at, 'strftime'):
                    try:
                        date_str = updated_at.strftime("%d/%m/%Y às %H:%M")
                    except:
                        date_str = str(updated_at)
                elif isinstance(updated_at, str):
                    try:
                        from datetime import datetime
                        if 'T' in updated_at:
                            date_clean = updated_at.replace('Z', '+00:00').split('+')[0].split('.')[0]
                            dt = datetime.fromisoformat(date_clean)
                            date_str = dt.strftime("%d/%m/%Y às %H:%M")
                        else:
                            date_str = updated_at
                    except:
                        date_str = updated_at
                else:
                    date_str = str(updated_at)
            else:
                date_str = 'N/A'
            
            # Criar texto do membro
            member_info = f"**GS Total:** {gs_total}\n"
            member_info += f"⚔️ AP: {ap} | 🔥 AAP: {aap} | 🛡️ DP: {dp}\n"
            member_info += f"🔗 **Link Gear:** {linkgear or 'Não informado'}\n"
            member_info += f"📅 **Última atualização:** {date_str}"
            
            # Adicionar campo (limite de 25 campos por embed do Discord)
            if i <= 25:
                embed.add_field(
                    name=f"#{i} - {family_name}",
                    value=member_info,
                    inline=False
                )
        
        if len(sorted_members) > 25:
            embed.set_footer(text=f"Mostrando 25 de {len(sorted_members)} membros")
        else:
            embed.set_footer(text=f"Total de {len(sorted_members)} membros")
        
        await interaction.followup.send(embed=embed, ephemeral=True)
        
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        logger.error(f"Erro ao buscar membros da classe: {error_details}")
        
        if interaction.response.is_done():
            await interaction.followup.send(
                f"❌ Erro ao buscar membros da classe: {str(e)}",
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"❌ Erro ao buscar membros da classe: {str(e)}",
                ephemeral=True
            )

@bot.tree.command(name="enviar_dm", description="Envia uma mensagem direta (DM) para um usuário")
@app_commands.describe(
    usuario="Usuário que receberá a mensagem",
    mensagem="Mensagem a ser enviada"
)
async def enviar_dm(interaction: discord.Interaction, usuario: discord.Member, mensagem: str):
    """Envia uma DM para um usuário (apenas administradores)"""
    try:
        embed = discord.Embed(
            title="📨 Mensagem da Staff",
            description=mensagem,
            color=discord.Color.blue(),
            timestamp=discord.utils.utcnow()
        )
        embed.set_footer(text="Staff Mouz")
        
        await usuario.send(embed=embed)
        
        await interaction.response.send_message(
            f"✅ Mensagem enviada para {usuario.mention} via DM!",
            ephemeral=True
        )
    except discord.Forbidden:
        await interaction.response.send_message(
            f"❌ Não foi possível enviar DM para {usuario.mention}. O usuário pode ter DMs desabilitadas ou bloqueou o bot.",
            ephemeral=True
        )
    except Exception as e:
        await interaction.response.send_message(
            f"❌ Erro ao enviar DM: {str(e)}",
            ephemeral=True
        )

@bot.tree.command(name="gearscore_dm", description="Envia seu gearscore via DM")
async def gearscore_dm(interaction: discord.Interaction):
    """Envia o gearscore do usuário via DM"""
    try:
        user_id = str(interaction.user.id)
        results = db.get_gearscore(user_id)
        
        if not results:
            await interaction.response.send_message(
                "❌ Nenhum gearscore encontrado! Use `/registro` para registrar seu gearscore.",
                ephemeral=True
            )
            return
        
        # Enviar resposta inicial
        await interaction.response.send_message(
            "📨 Enviando seu gearscore via DM...",
            ephemeral=True
        )
        
        # Enviar via DM (só pode ter 1 resultado agora)
        result = results[0]
        
        # Formatar dados dependendo do banco
        if isinstance(result, dict):
            family_name = result.get('family_name', 'N/A')
            class_pvp = result.get('class_pvp', 'N/A')
            ap = result.get('ap', 0)
            aap = result.get('aap', 0)
            dp = result.get('dp', 0)
            linkgear = result.get('linkgear', 'N/A')
            updated_at = result.get('updated_at', 'N/A')
        else:
            # Ordem das colunas: id(0), user_id(1), family_name(2), character_name(3), class_pvp(4), ap(5), aap(6), dp(7), linkgear(8), updated_at(9)
            family_name = result[2] if len(result) > 2 else 'N/A'
            class_pvp = result[4] if len(result) > 4 else 'N/A'
            ap = result[5] if len(result) > 5 else 0
            aap = result[6] if len(result) > 6 else 0
            dp = result[7] if len(result) > 7 else 0
            linkgear = result[8] if len(result) > 8 else 'N/A'
            updated_at = result[9] if len(result) > 9 else 'N/A'
        
        gs_total = calculate_gs(ap, aap, dp)
        embed = discord.Embed(
            title=f"📊 Gearscore - {class_pvp}",
            color=discord.Color.blue(),
            timestamp=discord.utils.utcnow()
        )
        embed.add_field(name="👤 Família", value=family_name, inline=True)
        embed.add_field(name="🎭 Classe PVP", value=class_pvp, inline=True)
        embed.add_field(name="⚔️ AP", value=f"{ap}", inline=True)
        embed.add_field(name="🔥 AAP", value=f"{aap}", inline=True)
        embed.add_field(name="🛡️ DP", value=f"{dp}", inline=True)
        embed.add_field(name="📊 GS Total", value=f"**{gs_total}** (MAX({ap}, {aap}) + {dp})", inline=False)
        embed.add_field(name="🔗 Link Gear", value=linkgear or "Não informado", inline=False)
        embed.set_footer(text=f"Última atualização: {updated_at}")
        
        await interaction.user.send(embed=embed)
            
    except discord.Forbidden:
        await interaction.followup.send(
            "❌ Não foi possível enviar DM. Verifique se você tem DMs habilitadas ou se não bloqueou o bot.",
            ephemeral=True
        )
    except Exception as e:
        await interaction.followup.send(
            f"❌ Erro ao enviar gearscore via DM: {str(e)}",
            ephemeral=True
        )

# Autocomplete para canais de voz
async def voice_channel_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    """Autocomplete para canais de voz do servidor"""
    if not interaction.guild:
        return []
    
    # Buscar todos os canais de voz
    voice_channels = [
        channel for channel in interaction.guild.channels 
        if isinstance(channel, discord.VoiceChannel)
    ]
    
    # Filtrar por nome se houver texto digitado
    if current:
        filtered = [
            channel for channel in voice_channels
            if current.lower() in channel.name.lower()
        ][:25]
    else:
        filtered = voice_channels[:25]
    
    return [
        app_commands.Choice(name=channel.name, value=str(channel.id))
        for channel in filtered
    ]

# Tipos de eventos disponíveis
TIPOS_EVENTO = ["GvG", "Treino"]

@bot.tree.command(name="lista", description="Cria uma lista dos membros em um canal de voz e registra participação")
@app_commands.describe(
    sala="Canal de voz para listar os membros (digite para buscar)",
    nome_lista="Nome da lista/evento",
    tipo="Tipo do evento (GvG, Treino, etc) - opcional para registrar participação"
)
@app_commands.autocomplete(sala=voice_channel_autocomplete)
@app_commands.choices(tipo=[
    app_commands.Choice(name=t, value=t) for t in TIPOS_EVENTO
])
async def lista(interaction: discord.Interaction, sala: str, nome_lista: str, tipo: str = None):
    """Cria uma lista dos membros conectados em um canal de voz e envia para o canal de listas"""
    try:
        if not interaction.guild:
            await interaction.response.send_message(
                "❌ Este comando só pode ser usado em um servidor!",
                ephemeral=True
            )
            return
        
        await interaction.response.defer(ephemeral=True)
        
        # Buscar o canal de voz
        voice_channel = interaction.guild.get_channel(int(sala))
        if not voice_channel or not isinstance(voice_channel, discord.VoiceChannel):
            await interaction.followup.send(
                "❌ Canal de voz não encontrado!",
                ephemeral=True
            )
            return
        
        # Buscar membros conectados no canal de voz
        all_members_in_voice = [
            member for member in voice_channel.members
            if not member.bot  # Excluir bots
        ]
        
        # Filtrar apenas membros com cargo da guilda
        members_in_voice = [
            member for member in all_members_in_voice
            if has_guild_role(member)
        ]
        
        # Contar membros removidos (sem cargo da guilda)
        members_removed = len(all_members_in_voice) - len(members_in_voice)
        
        if not members_in_voice:
            await interaction.followup.send(
                f"❌ Nenhum membro com cargo da guilda encontrado no canal de voz **{voice_channel.name}**!\n"
                f"ℹ️ {members_removed} membro(s) sem cargo da guilda foram ignorados.",
                ephemeral=True
            )
            return
        
        # Buscar o canal de destino
        list_channel = bot.get_channel(LIST_CHANNEL_ID)
        if not list_channel:
            list_channel = await bot.fetch_channel(LIST_CHANNEL_ID)
        
        if not list_channel:
            await interaction.followup.send(
                "❌ Canal de listas não encontrado!",
                ephemeral=True
            )
            return
        
        # Registrar participação se tipo foi informado
        evento_registrado = False
        if tipo:
            try:
                # Preparar lista de participantes
                participantes = []
                for member in members_in_voice:
                    # Buscar family_name do registro
                    user_data = db.get_user_current_data(str(member.id))
                    family_name = user_data[0] if user_data else None
                    
                    participantes.append({
                        'user_id': str(member.id),
                        'family_name': family_name,
                        'display_name': member.display_name
                    })
                
                # Registrar evento
                evento_id, qtd = db.registrar_evento(
                    tipo=tipo,
                    nome=nome_lista,
                    canal_voz=voice_channel.name,
                    criado_por=str(interaction.user.id),
                    criado_por_nome=interaction.user.display_name,
                    participantes=participantes
                )
                evento_registrado = True
                logger.info(f"Evento '{nome_lista}' ({tipo}) registrado com {qtd} participantes por {interaction.user.display_name}")
            except Exception as e:
                logger.error(f"Erro ao registrar evento: {e}")
        
        # Definir cor baseada no tipo
        cores_tipo = {
            "GvG": discord.Color.red(),
            "Treino": discord.Color.green(),
            "Node War": discord.Color.orange(),
            "Siege": discord.Color.purple(),
            "Boss": discord.Color.gold(),
            "Grind": discord.Color.teal(),
            "Outro": discord.Color.blue()
        }
        cor = cores_tipo.get(tipo, discord.Color.blue()) if tipo else discord.Color.blue()
        
        # Criar embed com a lista
        titulo = f"📋 {nome_lista}"
        if tipo:
            emojis_tipo = {"GvG": "⚔️", "Treino": "🏋️", "Node War": "🏰", "Siege": "🛡️", "Boss": "👹", "Grind": "💰", "Outro": "📌"}
            titulo = f"{emojis_tipo.get(tipo, '📋')} {nome_lista} ({tipo})"
        
        embed = discord.Embed(
            title=titulo,
            description=f"Lista de membros do canal de voz: **{voice_channel.mention}**",
            color=cor,
            timestamp=discord.utils.utcnow()
        )
        
        # Adicionar informações
        embed.add_field(
            name="🎤 Canal de Voz",
            value=voice_channel.mention,
            inline=True
        )
        
        embed.add_field(
            name="👥 Total de Membros",
            value=f"**{len(members_in_voice)}** membro(s)",
            inline=True
        )
        
        if tipo:
            embed.add_field(
                name="📊 Tipo",
                value=f"**{tipo}**",
                inline=True
            )
        
        # Formatar data e horário (fuso horário de Brasília)
        brasilia_tz = timezone('America/Sao_Paulo')
        now = datetime.now(brasilia_tz)
        date_str = now.strftime("%d/%m/%Y")
        time_str = now.strftime("%H:%M:%S")
        
        embed.add_field(
            name="📅 Data e Horário",
            value=f"**{date_str}** às **{time_str}**",
            inline=True
        )
        
        # Criar lista de membros
        members_list = ""
        for i, member in enumerate(members_in_voice, 1):
            members_list += f"{i}. {member.mention} ({member.display_name})\n"
        
        # Dividir em múltiplos campos se necessário (limite de 1024 caracteres por field)
        if len(members_list) > 1000:
            # Dividir a lista
            chunks = []
            current_chunk = ""
            for i, member in enumerate(members_in_voice, 1):
                line = f"{i}. {member.mention} ({member.display_name})\n"
                if len(current_chunk + line) > 1000:
                    chunks.append(current_chunk)
                    current_chunk = line
                else:
                    current_chunk += line
            
            if current_chunk:
                chunks.append(current_chunk)
            
            # Adicionar campos
            for i, chunk in enumerate(chunks, 1):
                field_name = "👥 Membros" if i == 1 else f"👥 Membros (cont.)"
                embed.add_field(
                    name=field_name,
                    value=chunk,
                    inline=False
                )
        else:
            embed.add_field(
                name="👥 Membros",
                value=members_list,
                inline=False
            )
        
        footer_text = f"Lista criada por {interaction.user.display_name}"
        if evento_registrado:
            footer_text += " | ✅ Participação registrada"
        if members_removed > 0:
            footer_text += f" | ⚠️ {members_removed} membro(s) sem cargo removido(s)"
        embed.set_footer(text=footer_text)
        
        # Enviar para o canal de listas
        await list_channel.send(embed=embed)
        
        msg_sucesso = f"✅ Lista **{nome_lista}** criada com sucesso e enviada para o canal de listas!"
        if evento_registrado:
            msg_sucesso += f"\n📊 **{len(members_in_voice)}** participações registradas para o tipo **{tipo}**"
        if members_removed > 0:
            msg_sucesso += f"\n⚠️ **{members_removed}** membro(s) sem cargo da guilda foram automaticamente removidos da lista"
        
        await interaction.followup.send(msg_sucesso, ephemeral=True)
        
    except ValueError:
        await interaction.followup.send(
            "❌ ID do canal de voz inválido!",
            ephemeral=True
        )
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        logger.error(f"Erro ao criar lista: {error_details}")
        
        if interaction.response.is_done():
            await interaction.followup.send(
                f"❌ Erro ao criar lista: {str(e)}",
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"❌ Erro ao criar lista: {str(e)}",
                ephemeral=True
            )

@bot.tree.command(name="relatorio_lista", description="[ADMIN] Mostra relatório de participação em eventos do mês")
async def relatorio_lista(interaction: discord.Interaction):
    """Mostra relatório de participação em eventos (GvG, Treino, etc) do mês atual"""
    if not is_admin_user(interaction.user):
        await interaction.response.send_message(
            "❌ Apenas administradores podem usar este comando!",
            ephemeral=True
        )
        return
    
    try:
        await interaction.response.defer(ephemeral=True)
        
        # Buscar relatório do mês atual
        relatorio = db.get_relatorio_participacoes()
        
        if relatorio['total_eventos'] == 0:
            await interaction.followup.send(
                "📊 **Relatório de Participação**\n\n"
                "❌ Nenhum evento registrado neste mês ainda.\n\n"
                "💡 Use `/lista` com o parâmetro `tipo` para registrar participações.",
                ephemeral=True
            )
            return
        
        # Formatar mês de referência
        from datetime import datetime
        mes_ref = relatorio['mes']
        ano, mes = mes_ref.split('-')
        meses_nome = {
            '01': 'Janeiro', '02': 'Fevereiro', '03': 'Março', '04': 'Abril',
            '05': 'Maio', '06': 'Junho', '07': 'Julho', '08': 'Agosto',
            '09': 'Setembro', '10': 'Outubro', '11': 'Novembro', '12': 'Dezembro'
        }
        mes_nome = f"{meses_nome.get(mes, mes)}/{ano}"
        
        # Criar embed principal
        embed = discord.Embed(
            title=f"📊 Relatório de Participação - {mes_nome}",
            description="Resumo de eventos e participações do mês",
            color=discord.Color.gold(),
            timestamp=discord.utils.utcnow()
        )
        
        # Resumo de eventos
        eventos_texto = ""
        emojis_tipo = {"GvG": "⚔️", "Treino": "🏋️", "Node War": "🏰", "Siege": "🛡️", "Boss": "👹", "Grind": "💰", "Outro": "📌"}
        for tipo, qtd in relatorio['eventos_por_tipo'].items():
            emoji = emojis_tipo.get(tipo, "📌")
            eventos_texto += f"{emoji} **{tipo}:** {qtd} evento(s)\n"
        
        embed.add_field(
            name=f"📅 Total de Eventos: {relatorio['total_eventos']}",
            value=eventos_texto if eventos_texto else "Nenhum evento",
            inline=False
        )
        
        # Calcular participação total por player (apenas os que ainda têm cargo da guilda)
        players_participacao = []
        players_removidos_count = 0
        
        for user_id, dados in relatorio['participacoes_por_player'].items():
            # Verificar se o membro ainda tem o cargo da guilda
            try:
                member = interaction.guild.get_member(int(user_id))
                if not member or not has_guild_role(member):
                    # Player não tem mais o cargo, não incluir no relatório
                    players_removidos_count += 1
                    continue
            except (ValueError, AttributeError):
                # Se não conseguir verificar (membro saiu do servidor, etc), não incluir
                players_removidos_count += 1
                continue
            
            total = sum(v for k, v in dados.items() if k not in ['display_name', 'family_name'])
            players_participacao.append({
                'user_id': user_id,
                'display_name': dados.get('display_name', user_id),
                'family_name': dados.get('family_name'),
                'total': total,
                'detalhes': dados
            })
        
        # Ordenar por total de participações
        players_participacao.sort(key=lambda x: x['total'], reverse=True)
        
        # Top 20 participantes
        top_players_texto = ""
        for i, player in enumerate(players_participacao[:20], 1):
            nome = player['family_name'] or player['display_name']
            
            # Montar detalhes por tipo
            detalhes = []
            for tipo in TIPOS_EVENTO:
                if tipo in player['detalhes']:
                    detalhes.append(f"{tipo}: {player['detalhes'][tipo]}")
            
            detalhes_str = " | ".join(detalhes) if detalhes else ""
            top_players_texto += f"**{i}.** {nome} - **{player['total']}** ({detalhes_str})\n"
        
        if top_players_texto:
            # Dividir se muito grande
            if len(top_players_texto) > 1024:
                partes = [top_players_texto[i:i+1020] for i in range(0, len(top_players_texto), 1020)]
                for idx, parte in enumerate(partes[:2]):
                    nome_campo = "🏆 Top Participantes" if idx == 0 else "🏆 Top Participantes (cont.)"
                    embed.add_field(name=nome_campo, value=parte, inline=False)
            else:
                embed.add_field(name="🏆 Top Participantes", value=top_players_texto, inline=False)
        
        # Estatísticas
        if players_participacao:
            media = sum(p['total'] for p in players_participacao) / len(players_participacao)
            embed.add_field(
                name="📈 Estatísticas",
                value=f"👥 **Total de players:** {len(players_participacao)}\n"
                      f"📊 **Média de participações:** {media:.1f}",
                inline=False
            )
        
        embed.set_footer(text=f"Relatório gerado por {interaction.user.display_name} | Reset no dia 1 de cada mês | Apenas membros com cargo da guilda")
        
        await interaction.followup.send(embed=embed, ephemeral=True)
        
    except Exception as e:
        import traceback
        logger.error(f"Erro ao gerar relatório: {traceback.format_exc()}")
        if interaction.response.is_done():
            await interaction.followup.send(
                f"❌ Erro ao gerar relatório: {str(e)}",
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"❌ Erro ao gerar relatório: {str(e)}",
                ephemeral=True
            )

@bot.tree.command(name="mover_sala", description="[ADMIN] Move todos os membros de uma sala de voz para outra")
@app_commands.describe(
    sala_origem="Canal de voz de origem (digite para buscar)",
    sala_destino="Canal de voz de destino (digite para buscar)"
)
@app_commands.autocomplete(sala_origem=voice_channel_autocomplete)
@app_commands.autocomplete(sala_destino=voice_channel_autocomplete)
async def mover_sala(interaction: discord.Interaction, sala_origem: str, sala_destino: str):
    """Move todos os membros de uma sala de voz para outra (apenas administradores)"""
    if not is_admin_user(interaction.user):
        await interaction.response.send_message(
            "❌ Apenas administradores podem usar este comando!",
            ephemeral=True
        )
        return
    
    try:
        if not interaction.guild:
            await interaction.response.send_message(
                "❌ Este comando só pode ser usado em um servidor!",
                ephemeral=True
            )
            return
        
        await interaction.response.defer(ephemeral=True)
        
        # Buscar os canais de voz
        origin_channel = interaction.guild.get_channel(int(sala_origem))
        destination_channel = interaction.guild.get_channel(int(sala_destino))
        
        if not origin_channel or not isinstance(origin_channel, discord.VoiceChannel):
            await interaction.followup.send(
                "❌ Canal de voz de origem não encontrado!",
                ephemeral=True
            )
            return
        
        if not destination_channel or not isinstance(destination_channel, discord.VoiceChannel):
            await interaction.followup.send(
                "❌ Canal de voz de destino não encontrado!",
                ephemeral=True
            )
            return
        
        if origin_channel.id == destination_channel.id:
            await interaction.followup.send(
                "❌ Os canais de origem e destino não podem ser o mesmo!",
                ephemeral=True
            )
            return
        
        # Buscar membros no canal de origem
        members_to_move = [
            member for member in origin_channel.members
            if not member.bot  # Excluir bots
        ]
        
        if not members_to_move:
            await interaction.followup.send(
                f"❌ Nenhum membro encontrado no canal de voz **{origin_channel.name}**!",
                ephemeral=True
            )
            return
        
        # Mover membros
        moved_count = 0
        failed_members = []
        
        for member in members_to_move:
            try:
                await member.move_to(destination_channel, reason=f"Movido por {interaction.user.display_name}")
                moved_count += 1
            except discord.Forbidden:
                failed_members.append((member, "Sem permissão para mover"))
            except discord.HTTPException as e:
                failed_members.append((member, str(e)))
            except Exception as e:
                failed_members.append((member, str(e)))
                logger.warning(f"Erro ao mover {member.display_name} (ID: {member.id}): {str(e)}")
        
        # Criar embed com resultado
        embed = discord.Embed(
            title="🔄 Movimentação de Membros",
            description=f"Resultado da movimentação de membros entre salas de voz",
            color=discord.Color.blue(),
            timestamp=discord.utils.utcnow()
        )
        
        embed.add_field(
            name="📤 Canal de Origem",
            value=origin_channel.mention,
            inline=True
        )
        
        embed.add_field(
            name="📥 Canal de Destino",
            value=destination_channel.mention,
            inline=True
        )
        
        embed.add_field(
            name="✅ Movidos com Sucesso",
            value=f"**{moved_count}** membro(s)",
            inline=True
        )
        
        if failed_members:
            embed.add_field(
                name="❌ Falhas",
                value=f"**{len(failed_members)}** membro(s) não puderam ser movidos",
                inline=True
            )
            
            # Lista de falhas (limitada)
            failed_list = ""
            for member, reason in failed_members[:10]:  # Limitar a 10 para não exceder
                failed_list += f"• {member.mention} - {reason}\n"
            
            if len(failed_members) > 10:
                failed_list += f"\n... e mais {len(failed_members) - 10} membro(s)"
            
            if failed_list:
                embed.add_field(
                    name="🚫 Membros que Falharam",
                    value=failed_list,
                    inline=False
                )
        
        embed.set_footer(text=f"Movimentação executada por {interaction.user.display_name}")
        
        await interaction.followup.send(embed=embed, ephemeral=True)
        
        # Enviar log de movimentação para o canal de logs
        await send_move_log_to_channel(
            bot, interaction, origin_channel, destination_channel,
            moved_count, len(failed_members), failed_members
        )
        
    except ValueError:
        await interaction.followup.send(
            "❌ ID do canal de voz inválido!",
            ephemeral=True
        )
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        logger.error(f"Erro ao mover membros: {error_details}")
        
        if interaction.response.is_done():
            await interaction.followup.send(
                f"❌ Erro ao mover membros: {str(e)}",
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"❌ Erro ao mover membros: {str(e)}",
                ephemeral=True
            )

@bot.tree.command(name="dm_cargo", description="Envia DM em massa para todos os membros com cargo(s) específico(s)")
@app_commands.describe(
    cargos="Mencione os cargos (ex: @Cargo1 @Cargo2) ou IDs separados por vírgula",
    mensagem="Mensagem a ser enviada",
    imagem="Imagem a ser enviada junto com a mensagem (opcional)"
)
async def dm_cargo(interaction: discord.Interaction, cargos: str, mensagem: str, imagem: discord.Attachment = None):
    """Envia DM para todos os membros com um ou mais cargos específicos"""
    # Verificar permissão
    if not has_dm_permission(interaction.user):
        await interaction.response.send_message(
            "❌ Você não tem permissão para usar este comando! Apenas administradores ou membros com cargos autorizados podem usar.",
            ephemeral=True
        )
        return
    
    await interaction.response.defer(ephemeral=True)
    
    try:
        # Extrair IDs de cargos da string (formato: <@&123456789> ou 123456789,987654321)
        import re
        role_ids = []
        
        # Buscar menções de cargos: <@&ID>
        mentions = re.findall(r'<@&(\d+)>', cargos)
        role_ids.extend(mentions)
        
        # Buscar IDs numéricos separados por vírgula ou espaço
        numeric_ids = re.findall(r'\d+', cargos.replace(',', ' '))
        role_ids.extend(numeric_ids)
        
        # Remover duplicatas
        role_ids = list(set(role_ids))
        
        if not role_ids:
            await interaction.followup.send(
                "❌ Nenhum cargo válido encontrado! Mencione os cargos (ex: @Cargo1 @Cargo2) ou forneça os IDs.",
                ephemeral=True
            )
            return
        
        # Buscar os cargos no servidor
        roles = []
        for role_id in role_ids:
            role = interaction.guild.get_role(int(role_id))
            if role:
                roles.append(role)
        
        if not roles:
            await interaction.followup.send(
                "❌ Nenhum cargo válido encontrado no servidor!",
                ephemeral=True
            )
            return
        
        # Buscar todos os membros que têm pelo menos um dos cargos
        members_with_roles = set()
        for role in roles:
            for member in interaction.guild.members:
                if role in member.roles and not member.bot:
                    members_with_roles.add(member)
        
        if not members_with_roles:
            role_mentions = ', '.join([role.mention for role in roles])
            await interaction.followup.send(
                f"❌ Nenhum membro encontrado com os cargos: {role_mentions}",
                ephemeral=True
            )
            return
        
        # Validar se a imagem é uma imagem válida
        image_url = None
        image_bytes = None
        image_filename = None
        
        if imagem:
            # Verificar se é uma imagem
            if not imagem.content_type or not imagem.content_type.startswith('image/'):
                await interaction.followup.send(
                    "❌ O arquivo anexado não é uma imagem válida!",
                    ephemeral=True
                )
                return
            
            # Baixar a imagem
            try:
                image_bytes = await imagem.read()
                image_filename = imagem.filename or "image.png"
                # Usar URL para embed
                image_url = imagem.url
            except Exception as e:
                await interaction.followup.send(
                    f"❌ Erro ao processar a imagem: {str(e)}",
                    ephemeral=True
                )
                return
        
        embed = discord.Embed(
            title="📨 Mensagem do Bot",
            description=mensagem,
            color=discord.Color.blue(),
            timestamp=discord.utils.utcnow()
        )
        
        # Adicionar imagem ao embed se houver
        if image_url:
            embed.set_image(url=image_url)
        
        # Footer nas DMs sempre mostra "Staff Mouz"
        embed.set_footer(text="Staff Mouz")
        
        sent = 0
        failed = 0
        blocked_members = []  # Lista de quem não recebeu
        success_members = []  # Lista de quem recebeu com sucesso
        
        for member in members_with_roles:
            try:
                # Enviar com imagem se houver
                if image_bytes:
                    # Criar nova instância do arquivo para cada envio
                    image_file = discord.File(
                        io.BytesIO(image_bytes),
                        filename=image_filename
                    )
                    await member.send(embed=embed, file=image_file)
                else:
                    await member.send(embed=embed)
                sent += 1
                success_members.append(member)
            except discord.Forbidden:
                failed += 1
                blocked_members.append(member)
            except Exception as e:
                failed += 1
                blocked_members.append(member)
                logger.warning(f"Erro ao enviar DM para {member.display_name} (ID: {member.id}): {str(e)}")
        
        # Criar relatório detalhado
        role_mentions = ', '.join([role.mention for role in roles])
        report_embed = discord.Embed(
            title="📊 Relatório de Envio de DMs",
            description=f"Resultado do envio para membros com os cargos: {role_mentions}",
            color=discord.Color.blue(),
            timestamp=discord.utils.utcnow()
        )
        
        report_embed.add_field(
            name="✅ Enviadas com Sucesso",
            value=f"**{sent}** membro(s) receberam a DM",
            inline=True
        )
        
        report_embed.add_field(
            name="❌ Não Receberam",
            value=f"**{failed}** membro(s) não receberam (DMs desabilitadas ou bot bloqueado)",
            inline=True
        )
        
        # Lista de quem não recebeu
        if blocked_members:
            blocked_list = ""
            for member in blocked_members[:50]:  # Limite de 50 para não exceder
                blocked_list += f"• {member.mention} ({member.display_name})\n"
            
            if len(blocked_members) > 50:
                blocked_list += f"\n... e mais {len(blocked_members) - 50} membro(s)"
            
            # Dividir em chunks se necessário (limite de 1024 caracteres por field)
            if len(blocked_list) > 1024:
                # Dividir a lista
                chunks = [blocked_list[i:i+1024] for i in range(0, len(blocked_list), 1024)]
                for i, chunk in enumerate(chunks):
                    field_name = "🚫 Membros que Não Receberam" if i == 0 else f"🚫 Membros que Não Receberam (cont.)"
                    report_embed.add_field(
                        name=field_name,
                        value=chunk,
                        inline=False
                    )
            else:
                report_embed.add_field(
                    name="🚫 Membros que Não Receberam a DM",
                    value=blocked_list,
                    inline=False
                )
        
        report_embed.set_footer(text=f"Envio executado por {interaction.user.display_name}")
        
        await interaction.followup.send(embed=report_embed, ephemeral=True)
        
        # Enviar lista pública no canal de relatórios (em formato embed)
        try:
            report_channel = bot.get_channel(DM_REPORT_CHANNEL_ID)
            if not report_channel:
                report_channel = await bot.fetch_channel(DM_REPORT_CHANNEL_ID)
            
            if report_channel:
                role_mentions = ', '.join([role.mention for role in roles])
                
                # Criar embed principal
                main_embed = discord.Embed(
                    title="📨 Relatório de Envio de DMs",
                    description=f"Resultado do envio de mensagens para membros com os cargos: {role_mentions}",
                    color=discord.Color.blue(),
                    timestamp=discord.utils.utcnow()
                )
                
                # Adicionar estatísticas gerais
                main_embed.add_field(
                    name="📊 Estatísticas",
                    value=f"**Total de membros:** {len(members_with_roles)}\n"
                          f"**✅ Receberam:** {sent}\n"
                          f"**❌ Não receberam:** {failed}",
                    inline=False
                )
                
                main_embed.set_footer(text=f"Enviado por {interaction.user.display_name}")
                
                # Enviar embed principal
                await report_channel.send(embed=main_embed)
                
                # Criar embed com lista de quem recebeu
                if success_members:
                    success_embed = discord.Embed(
                        title="✅ Membros que Receberam a DM",
                        color=discord.Color.green(),
                        timestamp=discord.utils.utcnow()
                    )
                    
                    # Dividir lista em chunks para não exceder limite de 1024 caracteres por field
                    members_list = ""
                    field_count = 0
                    
                    for i, member in enumerate(success_members, 1):
                        line = f"{i}. {member.display_name} ✅\n"
                        
                        # Se adicionar esta linha exceder o limite, criar novo field
                        if len(members_list + line) > 1000:  # Margem de segurança
                            field_count += 1
                            field_name = "✅ Receberam" if field_count == 1 else f"✅ Receberam (cont.)"
                            success_embed.add_field(
                                name=field_name,
                                value=members_list,
                                inline=False
                            )
                            members_list = line
                        else:
                            members_list += line
                    
                    # Adicionar último field se houver conteúdo
                    if members_list:
                        field_count += 1
                        field_name = "✅ Receberam" if field_count == 1 else f"✅ Receberam (cont.)"
                        success_embed.add_field(
                            name=field_name,
                            value=members_list,
                            inline=False
                        )
                    
                    # Se exceder 25 fields (limite do Discord), dividir em múltiplos embeds
                    if len(success_embed.fields) > 25:
                        # Enviar primeiro embed com até 25 fields
                        first_embed = discord.Embed(
                            title="✅ Membros que Receberam a DM (Parte 1)",
                            color=discord.Color.green(),
                            timestamp=discord.utils.utcnow()
                        )
                        for field in success_embed.fields[:25]:
                            first_embed.add_field(
                                name=field.name,
                                value=field.value,
                                inline=False
                            )
                        await report_channel.send(embed=first_embed)
                        
                        # Enviar segundo embed com o restante
                        if len(success_embed.fields) > 25:
                            second_embed = discord.Embed(
                                title="✅ Membros que Receberam a DM (Parte 2)",
                                color=discord.Color.green(),
                                timestamp=discord.utils.utcnow()
                            )
                            for field in success_embed.fields[25:]:
                                second_embed.add_field(
                                    name=field.name,
                                    value=field.value,
                                    inline=False
                                )
                            await report_channel.send(embed=second_embed)
                    else:
                        await report_channel.send(embed=success_embed)
                
                # Criar embed com lista de quem falhou
                if blocked_members:
                    failed_embed = discord.Embed(
                        title="❌ Membros que Não Receberam a DM",
                        description="Bot bloqueado ou DMs desabilitadas",
                        color=discord.Color.red(),
                        timestamp=discord.utils.utcnow()
                    )
                    
                    # Dividir lista em chunks
                    members_list = ""
                    field_count = 0
                    
                    for i, member in enumerate(blocked_members, 1):
                        line = f"{i}. {member.display_name} ❌\n"
                        
                        if len(members_list + line) > 1000:
                            field_count += 1
                            field_name = "❌ Não receberam" if field_count == 1 else f"❌ Não receberam (cont.)"
                            failed_embed.add_field(
                                name=field_name,
                                value=members_list,
                                inline=False
                            )
                            members_list = line
                        else:
                            members_list += line
                    
                    # Adicionar último field
                    if members_list:
                        field_count += 1
                        field_name = "❌ Não receberam" if field_count == 1 else f"❌ Não receberam (cont.)"
                        failed_embed.add_field(
                            name=field_name,
                            value=members_list,
                            inline=False
                        )
                    
                    # Dividir em múltiplos embeds se necessário
                    if len(failed_embed.fields) > 25:
                        first_embed = discord.Embed(
                            title="❌ Membros que Não Receberam a DM (Parte 1)",
                            description="Bot bloqueado ou DMs desabilitadas",
                            color=discord.Color.red(),
                            timestamp=discord.utils.utcnow()
                        )
                        for field in failed_embed.fields[:25]:
                            first_embed.add_field(
                                name=field.name,
                                value=field.value,
                                inline=False
                            )
                        await report_channel.send(embed=first_embed)
                        
                        if len(failed_embed.fields) > 25:
                            second_embed = discord.Embed(
                                title="❌ Membros que Não Receberam a DM (Parte 2)",
                                description="Bot bloqueado ou DMs desabilitadas",
                                color=discord.Color.red(),
                                timestamp=discord.utils.utcnow()
                            )
                            for field in failed_embed.fields[25:]:
                                second_embed.add_field(
                                    name=field.name,
                                    value=field.value,
                                    inline=False
                                )
                            await report_channel.send(embed=second_embed)
                    else:
                        await report_channel.send(embed=failed_embed)
                        
        except Exception as e:
            logger.error(f"Erro ao enviar relatório no canal (ID: {DM_REPORT_CHANNEL_ID}): {str(e)}")
    except Exception as e:
        await interaction.followup.send(
            f"❌ Erro ao enviar DMs: {str(e)}",
            ephemeral=True
        )

# Comandos removidos: dm_online e dm_todos

# ============================================
# COMANDOS ADMINISTRATIVOS
# ============================================

@bot.tree.command(name="admin_lista_classe", description="[ADMIN] Lista todos os membros de uma classe específica")
@app_commands.describe(
    classe="Classe a ser listada (digite para buscar)"
)
@app_commands.autocomplete(classe=classe_autocomplete)
async def admin_lista_classe(interaction: discord.Interaction, classe: str):
    """Lista todos os membros de uma classe específica (apenas administradores)"""
    if not is_admin_user(interaction.user):
        await interaction.response.send_message(
            "❌ Apenas administradores podem usar este comando!",
            ephemeral=True
        )
        return
    
    if classe not in BDO_CLASSES:
        await interaction.response.send_message(
            f"❌ Classe inválida! Use `/estatisticas_classes` para ver as classes disponíveis.",
            ephemeral=True
        )
        return
    
    try:
        # Deferir resposta antes de operações que podem demorar
        await interaction.response.defer(ephemeral=True)
        
        # Buscar apenas membros que têm o cargo da guilda
        valid_user_ids = await get_guild_member_ids(interaction.guild)
        members = db.get_class_members(classe, valid_user_ids=valid_user_ids)
        
        if not members:
            await interaction.followup.send(
                f"❌ Nenhum membro encontrado com a classe {classe} (apenas membros com cargo da guilda)",
                ephemeral=True
            )
            return
        
        embed = discord.Embed(
            title=f"👥 Membros - {classe}",
            description=f"Total: **{len(members)}** membro(s)",
            color=discord.Color.blue(),
            timestamp=discord.utils.utcnow()
        )
        
        # Mostrar até 25 membros (limite do Discord)
        for i, member in enumerate(members[:25], 1):
            if isinstance(member, dict):
                family = member.get('family_name', 'N/A')
                ap = int(member.get('ap', 0) or 0)
                aap = int(member.get('aap', 0) or 0)
                dp = int(member.get('dp', 0) or 0)
            else:
                # Ordem das colunas: id(0), user_id(1), family_name(2), character_name(3), class_pvp(4), ap(5), aap(6), dp(7), linkgear(8), updated_at(9)
                family = member[2] if len(member) > 2 else 'N/A'
                ap = int(member[5] or 0) if len(member) > 5 else 0
                aap = int(member[6] or 0) if len(member) > 6 else 0
                dp = int(member[7] or 0) if len(member) > 7 else 0
            
            total_gs = calculate_gs(ap, aap, dp)
            embed.add_field(
                name=f"{i}. {family}",
                value=f"👤 {family}\n⚔️ AP: {ap} | 🔥 AAP: {aap} | 🛡️ DP: {dp}\n📊 **Total: {total_gs}**",
                inline=False
            )
        
        if len(members) > 25:
            embed.set_footer(text=f"Mostrando 25 de {len(members)} membros")
        
        await interaction.followup.send(embed=embed, ephemeral=True)
        
    except Exception as e:
        # Verificar se já respondeu
        if interaction.response.is_done():
            await interaction.followup.send(
                f"❌ Erro ao buscar membros: {str(e)}",
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"❌ Erro ao buscar membros: {str(e)}",
                ephemeral=True
            )

@bot.tree.command(name="admin_excluir_registro", description="[ADMIN] Exclui o registro de gearscore de um membro")
@app_commands.describe(
    usuario="Usuário do Discord para excluir o registro",
    confirmar="Digite 'CONFIRMAR' para executar a exclusão (case-sensitive)"
)
async def admin_excluir_registro(interaction: discord.Interaction, usuario: discord.Member, confirmar: str):
    """Exclui o registro de gearscore de um membro (apenas administradores)"""
    if not is_admin_user(interaction.user):
        await interaction.response.send_message(
            "❌ Apenas administradores podem usar este comando!",
            ephemeral=True
        )
        return
    
    # Verificar confirmação
    if confirmar != "CONFIRMAR":
        await interaction.response.send_message(
            "❌ **Operação não confirmada!**\n\n"
            "Para excluir o registro, você precisa digitar exatamente `CONFIRMAR` no campo de confirmação.\n"
            "⚠️ **Atenção:** Esta ação é **irreversível** e excluirá todos os dados e histórico do membro!",
            ephemeral=True
        )
        return
    
    try:
        await interaction.response.defer(ephemeral=True)
        
        user_id = str(usuario.id)
        
        # Buscar dados antes de excluir (para log)
        current_data = db.get_user_current_data(user_id)
        
        # Excluir registro
        success, message = db.delete_user_gearscore(user_id)
        
        if success:
            logger.info(f"Comando /admin_excluir_registro executado por {interaction.user.display_name} (ID: {interaction.user.id}) - Excluiu registro de {usuario.display_name} (ID: {user_id})")
            
            # Remover cargo de registrado e adicionar cargo de não registrado
            member = interaction.guild.get_member(usuario.id)
            if member:
                await update_registration_roles(member, False)
            
            await interaction.followup.send(
                f"✅ **Registro excluído com sucesso!**\n\n"
                f"👤 **Usuário:** {usuario.mention}\n"
                f"📝 **Mensagem:** {message}\n\n"
                f"⚠️ O membro precisará fazer um novo `/registro` para ter seus dados novamente.",
                ephemeral=True
            )
        else:
            await interaction.followup.send(
                f"❌ **Erro ao excluir registro:**\n{message}",
                ephemeral=True
            )
    
    except Exception as e:
        logger.error(f"Erro ao excluir registro: {str(e)}")
        if interaction.response.is_done():
            await interaction.followup.send(
                f"❌ Erro ao excluir registro: {str(e)}",
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"❌ Erro ao excluir registro: {str(e)}",
                ephemeral=True
            )

@bot.tree.command(name="admin_alterar_registro", description="[ADMIN] Altera o registro de gearscore de um membro")
@app_commands.describe(
    usuario="Usuário do Discord para alterar o registro",
    nome_familia="Novo nome da família (deixe vazio para manter atual)",
    nome_personagem="Novo nome do personagem (deixe vazio para manter atual)",
    classe_pvp="Nova classe PVP (deixe vazio para manter atual)",
    ap="Novo AP (deixe vazio para manter atual)",
    aap="Novo AAP (deixe vazio para manter atual)",
    dp="Novo DP (deixe vazio para manter atual)",
    linkgear="Novo link do gear (deixe vazio para manter atual)"
)
@app_commands.autocomplete(classe_pvp=classe_autocomplete)
async def admin_alterar_registro(
    interaction: discord.Interaction,
    usuario: discord.Member,
    nome_familia: str = None,
    nome_personagem: str = None,
    classe_pvp: str = None,
    ap: int = None,
    aap: int = None,
    dp: int = None,
    linkgear: str = None
):
    """Altera o registro de gearscore de um membro (apenas administradores)"""
    if not is_admin_user(interaction.user):
        await interaction.response.send_message(
            "❌ Apenas administradores podem usar este comando!",
            ephemeral=True
        )
        return
    
    # Verificar se pelo menos um campo foi fornecido
    if all(v is None for v in [nome_familia, nome_personagem, classe_pvp, ap, aap, dp, linkgear]):
        await interaction.response.send_message(
            "❌ Você precisa fornecer pelo menos um campo para alterar!",
            ephemeral=True
        )
        return
    
    # Validar classe PVP se fornecida
    if classe_pvp is not None and classe_pvp not in BDO_CLASSES:
        classes_str = ", ".join(BDO_CLASSES[:10])
        await interaction.response.send_message(
            f"❌ Classe inválida! Classes disponíveis: {classes_str}... (use autocomplete para ver todas)",
            ephemeral=True
        )
        return
    
    # Validar valores numéricos se fornecidos
    if ap is not None and ap < 0:
        await interaction.response.send_message("❌ O valor de AP deve ser positivo!", ephemeral=True)
        return
    if aap is not None and aap < 0:
        await interaction.response.send_message("❌ O valor de AAP deve ser positivo!", ephemeral=True)
        return
    if dp is not None and dp < 0:
        await interaction.response.send_message("❌ O valor de DP deve ser positivo!", ephemeral=True)
        return
    
    try:
        await interaction.response.defer(ephemeral=True)
        
        user_id = str(usuario.id)
        
        # Buscar dados atuais para mostrar no log
        current_data = db.get_user_current_data(user_id)
        if not current_data:
            await interaction.followup.send(
                f"❌ {usuario.mention} não possui registro de gearscore!\n"
                f"Use `/registro_manual` para criar um novo registro.",
                ephemeral=True
            )
            return
        
        old_family_name, old_character_name, old_class_pvp = current_data
        
        # Atualizar registro
        success, message = db.admin_update_gearscore(
            user_id=user_id,
            family_name=nome_familia,
            character_name=nome_personagem,
            class_pvp=classe_pvp,
            ap=ap,
            aap=aap,
            dp=dp,
            linkgear=linkgear
        )
        
        if success:
            # Montar lista de campos alterados
            changed_fields = []
            if nome_familia is not None:
                changed_fields.append(f"Nome Família: {old_family_name} → {nome_familia}")
            if nome_personagem is not None:
                changed_fields.append(f"Nome Personagem: {old_character_name or 'N/A'} → {nome_personagem}")
            if classe_pvp is not None:
                changed_fields.append(f"Classe: {old_class_pvp} → {classe_pvp}")
            if ap is not None:
                changed_fields.append(f"AP: {ap}")
            if aap is not None:
                changed_fields.append(f"AAP: {aap}")
            if dp is not None:
                changed_fields.append(f"DP: {dp}")
            if linkgear is not None:
                changed_fields.append(f"LinkGear: atualizado")
            
            # Atualizar nickname se o nome de família foi alterado
            if nome_familia is not None:
                member = interaction.guild.get_member(usuario.id)
                if member:
                    nick_success, nick_msg = await update_member_nickname(member, nome_familia)
                    if nick_success:
                        changed_fields.append(f"Nickname: atualizado para {nome_familia}")
                    else:
                        changed_fields.append(f"Nickname: não atualizado ({nick_msg})")
            
            logger.info(f"Comando /admin_alterar_registro executado por {interaction.user.display_name} (ID: {interaction.user.id}) - Alterou registro de {usuario.display_name} (ID: {user_id})")
            
            await interaction.followup.send(
                f"✅ **Registro alterado com sucesso!**\n\n"
                f"👤 **Usuário:** {usuario.mention}\n"
                f"📝 **Alterações:**\n" + "\n".join([f"• {field}" for field in changed_fields]) + "\n\n"
                f"📊 **Resultado:** {message}",
                ephemeral=True
            )
        else:
            await interaction.followup.send(
                f"❌ **Erro ao alterar registro:**\n{message}",
                ephemeral=True
            )
    
    except Exception as e:
        logger.error(f"Erro ao alterar registro: {str(e)}")
        if interaction.response.is_done():
            await interaction.followup.send(
                f"❌ Erro ao alterar registro: {str(e)}",
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"❌ Erro ao alterar registro: {str(e)}",
                ephemeral=True
            )

@bot.tree.command(name="admin_sincronizar_nomes", description="[ADMIN] Sincroniza os nicknames de todos os membros com seus nomes de família")
async def admin_sincronizar_nomes(interaction: discord.Interaction):
    """Sincroniza os nicknames de todos os membros registrados com seus nomes de família (apenas administradores)"""
    if not is_admin_user(interaction.user):
        await interaction.response.send_message(
            "❌ Apenas administradores podem usar este comando!",
            ephemeral=True
        )
        return
    
    try:
        await interaction.response.defer(ephemeral=True)
        
        if not interaction.guild:
            await interaction.followup.send(
                "❌ Este comando só pode ser usado em um servidor!",
                ephemeral=True
            )
            return
        
        # Buscar apenas membros que têm o cargo da guilda
        valid_user_ids = await get_guild_member_ids(interaction.guild)
        
        if not valid_user_ids:
            await interaction.followup.send(
                "❌ Nenhum membro com o cargo da guilda encontrado!",
                ephemeral=True
            )
            return
        
        # Buscar todos os registros do banco de dados
        all_registered = db.get_all_gearscores(valid_user_ids=valid_user_ids)
        
        if not all_registered:
            await interaction.followup.send(
                "❌ Nenhum registro de gearscore encontrado!",
                ephemeral=True
            )
            return
        
        # Contadores
        success_count = 0
        error_count = 0
        skipped_count = 0
        errors_detail = []
        
        # Atualizar nickname de cada membro
        for record in all_registered:
            # Extrair dados do registro
            if isinstance(record, dict):
                user_id = record.get('user_id', '')
                family_name = record.get('family_name', '')
            else:
                # Ordem das colunas: id(0), user_id(1), family_name(2), character_name(3), class_pvp(4), ap(5), aap(6), dp(7), linkgear(8), updated_at(9)
                user_id = record[1] if len(record) > 1 else ''
                family_name = record[2] if len(record) > 2 else ''
            
            if not user_id or not family_name:
                skipped_count += 1
                continue
            
            # Buscar membro no servidor
            try:
                member = interaction.guild.get_member(int(user_id))
                if not member:
                    skipped_count += 1
                    continue
                
                # Verificar se já tem o nickname correto
                if member.nick == family_name:
                    skipped_count += 1
                    continue
                
                # Atualizar nickname
                nick_success, nick_msg = await update_member_nickname(member, family_name)
                if nick_success:
                    success_count += 1
                else:
                    error_count += 1
                    if len(errors_detail) < 10:  # Limitar detalhes de erro
                        errors_detail.append(f"{member.display_name}: {nick_msg}")
            except Exception as e:
                error_count += 1
                if len(errors_detail) < 10:
                    errors_detail.append(f"ID {user_id}: {str(e)}")
        
        # Criar embed de resultado
        embed = discord.Embed(
            title="✅ Sincronização de Nicknames Concluída!",
            color=discord.Color.green() if error_count == 0 else discord.Color.orange(),
            timestamp=discord.utils.utcnow()
        )
        
        embed.add_field(
            name="📊 Resultado",
            value=f"✅ **Atualizados:** {success_count}\n"
                  f"⏭️ **Ignorados:** {skipped_count} (já estavam corretos ou não encontrados)\n"
                  f"❌ **Erros:** {error_count}",
            inline=False
        )
        
        if errors_detail:
            embed.add_field(
                name="⚠️ Detalhes dos Erros",
                value="\n".join(errors_detail[:10]),
                inline=False
            )
        
        embed.set_footer(text=f"Executado por {interaction.user.display_name}")
        
        logger.info(f"Comando /admin_sincronizar_nomes executado por {interaction.user.display_name} (ID: {interaction.user.id}) - Sucesso: {success_count}, Erros: {error_count}, Ignorados: {skipped_count}")
        
        await interaction.followup.send(embed=embed, ephemeral=True)
    
    except Exception as e:
        logger.error(f"Erro ao sincronizar nomes: {str(e)}")
        if interaction.response.is_done():
            await interaction.followup.send(
                f"❌ Erro ao sincronizar nomes: {str(e)}",
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"❌ Erro ao sincronizar nomes: {str(e)}",
                ephemeral=True
            )

# ============================================
# COMANDOS DE INTEGRAÇÃO COM APOLLO (GS EVENTO)
# ============================================

@bot.tree.command(name="gs_evento", description="[ADMIN] Busca o GS dos participantes de um evento do Apollo")
@app_commands.describe(
    mensagem_id="ID da mensagem do Apollo (clique direito na mensagem > Copiar ID)"
)
async def gs_evento(interaction: discord.Interaction, mensagem_id: str):
    """Busca o GS dos participantes listados em uma mensagem do Apollo"""
    if not is_admin_user(interaction.user):
        await interaction.response.send_message(
            "❌ Apenas administradores podem usar este comando!",
            ephemeral=True
        )
        return
    
    try:
        await interaction.response.defer(ephemeral=True)
        
        # Buscar a mensagem
        try:
            message_id = int(mensagem_id.strip())
            message = await interaction.channel.fetch_message(message_id)
        except ValueError:
            await interaction.followup.send(
                "❌ ID da mensagem inválido! Deve ser um número.",
                ephemeral=True
            )
            return
        except discord.NotFound:
            await interaction.followup.send(
                "❌ Mensagem não encontrada! Certifique-se de usar o comando no mesmo canal da mensagem.",
                ephemeral=True
            )
            return
        
        # Extrair nomes dos embeds do Apollo
        all_names = []
        roles_data = {}  # {role_name: [names]}
        
        if message.embeds:
            for embed in message.embeds:
                # Extrair do título ou descrição
                if embed.description:
                    # Processar descrição linha por linha
                    for line in embed.description.split('\n'):
                        line = line.strip()
                        if line and not line.startswith(('🔒', '📅', '⏰', '🕐', '@', 'http', '[Add')):
                            # Limpar emojis e formatação
                            clean_name = line.strip('🔴🟢🟡⚪🔵⚫👤📍✅❌⭐🛡️⚔️🏹🔮💚🧡💜💙❤️🖤🤍💛 *_~`>')
                            clean_name = clean_name.strip()
                            if clean_name and len(clean_name) > 1 and not clean_name.startswith(('@', 'http')):
                                all_names.append(clean_name)
                
                # Extrair dos fields
                for field in embed.fields:
                    field_name = field.name.strip()
                    field_value = field.value.strip()
                    
                    # Extrair nomes do valor do field
                    names_in_field = []
                    for line in field_value.split('\n'):
                        line = line.strip()
                        if line and not line.startswith(('🔒', '@', 'http', '[Add', '`')):
                            # Limpar emojis e formatação
                            clean_name = line.strip('🔴🟢🟡⚪🔵⚫👤📍✅❌⭐🛡️⚔️🏹🔮💚🧡💜💙❤️🖤🤍💛 *_~`>-•')
                            clean_name = clean_name.strip()
                            if clean_name and len(clean_name) > 1 and not clean_name.startswith(('@', 'http')):
                                names_in_field.append(clean_name)
                                all_names.append(clean_name)
                    
                    if names_in_field:
                        # Limpar nome do field
                        clean_field_name = field_name.strip('🔴🟢🟡⚪🔵⚫👤📍✅❌⭐🛡️⚔️🏹🔮💚🧡💜💙❤️🖤🤍💛🗡️ *_~`()0123456789/')
                        clean_field_name = clean_field_name.strip()
                        if clean_field_name:
                            if clean_field_name not in roles_data:
                                roles_data[clean_field_name] = []
                            roles_data[clean_field_name].extend(names_in_field)
        
        if not all_names:
            await interaction.followup.send(
                "❌ Não foi possível extrair nomes da mensagem. Certifique-se de que é uma mensagem do Apollo com participantes.",
                ephemeral=True
            )
            return
        
        # Remover duplicatas mantendo ordem
        unique_names = list(dict.fromkeys(all_names))
        
        # Buscar GS de cada nome
        gs_results = db.get_gearscores_by_family_names(unique_names)
        
        # Calcular estatísticas
        found_players = []
        not_found_players = []
        total_gs = 0
        
        for name in unique_names:
            result = gs_results.get(name.lower())
            if result:
                # Ordem: id(0), user_id(1), family_name(2), character_name(3), class_pvp(4), ap(5), aap(6), dp(7)
                ap = result[5]
                aap = result[6]
                dp = result[7]
                gs = max(ap, aap) + dp
                class_pvp = result[4]
                found_players.append({
                    'name': result[2],  # family_name original do banco
                    'gs': gs,
                    'class': class_pvp,
                    'ap': ap,
                    'aap': aap,
                    'dp': dp
                })
                total_gs += gs
            else:
                not_found_players.append(name)
        
        # Criar embed de resultado
        embed = discord.Embed(
            title="📊 GS dos Participantes - Evento Apollo",
            color=discord.Color.blue(),
            timestamp=discord.utils.utcnow()
        )
        
        # Estatísticas gerais
        avg_gs = total_gs // len(found_players) if found_players else 0
        embed.add_field(
            name="📈 Estatísticas",
            value=f"**Total encontrados:** {len(found_players)}/{len(unique_names)}\n"
                  f"**Média GS:** {avg_gs}\n"
                  f"**Não registrados:** {len(not_found_players)}",
            inline=False
        )
        
        # Se temos dados por função (do Apollo)
        if roles_data:
            for role_name, role_names in roles_data.items():
                role_text = ""
                role_gs_total = 0
                role_count = 0
                
                for name in role_names:
                    result = gs_results.get(name.lower())
                    if result:
                        ap = result[5]
                        aap = result[6]
                        dp = result[7]
                        gs = max(ap, aap) + dp
                        class_pvp = result[4]
                        role_text += f"• **{result[2]}** - {gs} GS ({class_pvp})\n"
                        role_gs_total += gs
                        role_count += 1
                    else:
                        role_text += f"• ~~{name}~~ - *Não registrado*\n"
                
                if role_text:
                    role_avg = role_gs_total // role_count if role_count > 0 else 0
                    # Limitar tamanho do field
                    if len(role_text) > 1000:
                        role_text = role_text[:997] + "..."
                    embed.add_field(
                        name=f"{role_name} (Média: {role_avg} GS)",
                        value=role_text,
                        inline=False
                    )
        else:
            # Listar todos ordenados por GS
            found_players.sort(key=lambda x: x['gs'], reverse=True)
            
            players_text = ""
            for i, player in enumerate(found_players[:25], 1):  # Limitar a 25
                players_text += f"**{i}.** {player['name']} - **{player['gs']}** GS ({player['class']})\n"
            
            if players_text:
                embed.add_field(
                    name="🏆 Ranking por GS",
                    value=players_text[:1024],
                    inline=False
                )
        
        # Listar não encontrados
        if not_found_players:
            not_found_text = ", ".join(not_found_players[:20])
            if len(not_found_players) > 20:
                not_found_text += f"... (+{len(not_found_players) - 20})"
            embed.add_field(
                name="❌ Não Registrados",
                value=not_found_text[:1024],
                inline=False
            )
        
        embed.set_footer(text=f"Consultado por {interaction.user.display_name}")
        
        await interaction.followup.send(embed=embed, ephemeral=True)
        
    except Exception as e:
        logger.error(f"Erro ao buscar GS do evento: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        if interaction.response.is_done():
            await interaction.followup.send(
                f"❌ Erro ao buscar GS do evento: {str(e)}",
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"❌ Erro ao buscar GS do evento: {str(e)}",
                ephemeral=True
            )

# Modal para colar lista de nomes
class GSListaModal(discord.ui.Modal, title="📋 Buscar GS por Lista de Nomes"):
    nomes = discord.ui.TextInput(
        label="Nomes dos jogadores (um por linha)",
        style=discord.TextStyle.paragraph,
        placeholder="DaVila\nArehasa\nXr\nWendellNog",
        required=True,
        max_length=2000
    )
    
    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        
        # Processar nomes
        names_list = [n.strip() for n in self.nomes.value.split('\n') if n.strip()]
        
        if not names_list:
            await interaction.followup.send("❌ Nenhum nome fornecido!", ephemeral=True)
            return
        
        # Buscar GS de cada nome
        gs_results = db.get_gearscores_by_family_names(names_list)
        
        # Calcular estatísticas
        found_players = []
        not_found_players = []
        total_gs = 0
        
        for name in names_list:
            result = gs_results.get(name.lower())
            if result:
                ap = result[5]
                aap = result[6]
                dp = result[7]
                gs = max(ap, aap) + dp
                class_pvp = result[4]
                found_players.append({
                    'name': result[2],
                    'gs': gs,
                    'class': class_pvp
                })
                total_gs += gs
            else:
                not_found_players.append(name)
        
        # Ordenar por GS
        found_players.sort(key=lambda x: x['gs'], reverse=True)
        
        # Criar embed
        embed = discord.Embed(
            title="📊 GS dos Jogadores",
            color=discord.Color.blue(),
            timestamp=discord.utils.utcnow()
        )
        
        avg_gs = total_gs // len(found_players) if found_players else 0
        embed.add_field(
            name="📈 Estatísticas",
            value=f"**Encontrados:** {len(found_players)}/{len(names_list)}\n"
                  f"**Média GS:** {avg_gs}\n"
                  f"**Não registrados:** {len(not_found_players)}",
            inline=False
        )
        
        # Listar jogadores
        players_text = ""
        for i, player in enumerate(found_players, 1):
            players_text += f"**{i}.** {player['name']} - **{player['gs']}** GS ({player['class']})\n"
        
        if players_text:
            # Dividir em múltiplos fields se necessário
            if len(players_text) > 1024:
                parts = [players_text[i:i+1024] for i in range(0, len(players_text), 1024)]
                for idx, part in enumerate(parts[:3]):  # Máximo 3 parts
                    embed.add_field(
                        name=f"🏆 Ranking" + (f" (cont. {idx+1})" if idx > 0 else ""),
                        value=part,
                        inline=False
                    )
            else:
                embed.add_field(name="🏆 Ranking por GS", value=players_text, inline=False)
        
        if not_found_players:
            not_found_text = ", ".join(not_found_players[:15])
            if len(not_found_players) > 15:
                not_found_text += f"... (+{len(not_found_players) - 15})"
            embed.add_field(name="❌ Não Registrados", value=not_found_text, inline=False)
        
        embed.set_footer(text=f"Consultado por {interaction.user.display_name}")
        
        await interaction.followup.send(embed=embed, ephemeral=True)

@bot.tree.command(name="gs_lista", description="[ADMIN] Busca o GS de uma lista de jogadores (cole os nomes)")
async def gs_lista(interaction: discord.Interaction):
    """Abre um modal para colar uma lista de nomes e buscar o GS de cada um"""
    if not is_admin_user(interaction.user):
        await interaction.response.send_message(
            "❌ Apenas administradores podem usar este comando!",
            ephemeral=True
        )
        return
    
    await interaction.response.send_modal(GSListaModal())

@bot.tree.command(name="gs_media", description="[ADMIN] Calcula a média de GS de uma lista de jogadores")
@app_commands.describe(
    nomes="Nomes dos jogadores separados por vírgula (ex: DaVila, Arehasa, Xr)"
)
async def gs_media(interaction: discord.Interaction, nomes: str):
    """Calcula a média de GS de uma lista de jogadores pelo nome de família"""
    if not is_admin_user(interaction.user):
        await interaction.response.send_message(
            "❌ Apenas administradores podem usar este comando!",
            ephemeral=True
        )
        return
    
    try:
        await interaction.response.defer(ephemeral=True)
        
        # Processar nomes (separados por vírgula ou quebra de linha)
        names_list = []
        for part in nomes.replace('\n', ',').split(','):
            name = part.strip()
            if name:
                names_list.append(name)
        
        if not names_list:
            await interaction.followup.send("❌ Nenhum nome fornecido!", ephemeral=True)
            return
        
        # Buscar GS de cada nome
        gs_results = db.get_gearscores_by_family_names(names_list)
        
        # Calcular estatísticas
        found_players = []
        not_found_players = []
        total_gs = 0
        
        for name in names_list:
            result = gs_results.get(name.lower())
            if result:
                ap = result[5]
                aap = result[6]
                dp = result[7]
                gs = max(ap, aap) + dp
                class_pvp = result[4]
                found_players.append({
                    'name': result[2],
                    'gs': gs,
                    'class': class_pvp,
                    'ap': ap,
                    'aap': aap,
                    'dp': dp
                })
                total_gs += gs
            else:
                not_found_players.append(name)
        
        if not found_players:
            await interaction.followup.send(
                f"❌ Nenhum dos jogadores foi encontrado no banco de dados!\n"
                f"**Nomes buscados:** {', '.join(names_list)}",
                ephemeral=True
            )
            return
        
        # Calcular médias
        avg_gs = total_gs // len(found_players)
        avg_ap = sum(p['ap'] for p in found_players) // len(found_players)
        avg_aap = sum(p['aap'] for p in found_players) // len(found_players)
        avg_dp = sum(p['dp'] for p in found_players) // len(found_players)
        
        # Encontrar maior e menor GS
        found_players.sort(key=lambda x: x['gs'], reverse=True)
        highest = found_players[0]
        lowest = found_players[-1]
        
        # Criar embed
        embed = discord.Embed(
            title="📊 Média de GS do Grupo",
            color=discord.Color.gold(),
            timestamp=discord.utils.utcnow()
        )
        
        # Estatísticas principais
        embed.add_field(
            name="📈 Média Geral",
            value=f"**{avg_gs}** GS\n({avg_ap}/{avg_aap}/{avg_dp})",
            inline=True
        )
        
        embed.add_field(
            name="👥 Jogadores",
            value=f"**{len(found_players)}** encontrados\n**{len(not_found_players)}** não registrados",
            inline=True
        )
        
        embed.add_field(
            name="📉 Variação",
            value=f"🔺 Maior: **{highest['gs']}** ({highest['name']})\n"
                  f"🔻 Menor: **{lowest['gs']}** ({lowest['name']})",
            inline=True
        )
        
        # Lista de jogadores
        players_text = ""
        for player in found_players:
            diff = player['gs'] - avg_gs
            diff_str = f"+{diff}" if diff >= 0 else str(diff)
            players_text += f"• **{player['name']}** - {player['gs']} GS ({diff_str})\n"
        
        if len(players_text) > 1024:
            players_text = players_text[:1020] + "..."
        
        embed.add_field(
            name="🎮 Jogadores (ordenado por GS)",
            value=players_text,
            inline=False
        )
        
        # Não encontrados
        if not_found_players:
            not_found_text = ", ".join(not_found_players)
            if len(not_found_text) > 200:
                not_found_text = not_found_text[:200] + "..."
            embed.add_field(
                name="❌ Não Registrados",
                value=not_found_text,
                inline=False
            )
        
        embed.set_footer(text=f"Consultado por {interaction.user.display_name}")
        
        await interaction.followup.send(embed=embed, ephemeral=True)
        
    except Exception as e:
        logger.error(f"Erro ao calcular média de GS: {str(e)}")
        if interaction.response.is_done():
            await interaction.followup.send(
                f"❌ Erro ao calcular média: {str(e)}",
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"❌ Erro ao calcular média: {str(e)}",
                ephemeral=True
            )

@bot.tree.command(name="gs_abaixo_media", description="[ADMIN] Lista players abaixo do GS médio da guilda")
async def gs_abaixo_media(interaction: discord.Interaction):
    """Lista todos os players que estão abaixo do GS médio da guilda com suas builds"""
    if not is_admin_user(interaction.user):
        await interaction.response.send_message(
            "❌ Apenas administradores podem usar este comando!",
            ephemeral=True
        )
        return
    
    try:
        await interaction.response.defer(ephemeral=True)
        
        if not interaction.guild:
            await interaction.followup.send(
                "❌ Este comando só pode ser usado em um servidor!",
                ephemeral=True
            )
            return
        
        # Buscar apenas membros que têm o cargo da guilda
        valid_user_ids = await get_guild_member_ids(interaction.guild)
        
        if not valid_user_ids:
            await interaction.followup.send(
                "❌ Nenhum membro com o cargo da guilda encontrado!",
                ephemeral=True
            )
            return
        
        # Buscar todos os gearscores
        all_gearscores = db.get_all_gearscores(valid_user_ids=valid_user_ids)
        
        if not all_gearscores:
            await interaction.followup.send(
                "❌ Nenhum gearscore cadastrado ainda!",
                ephemeral=True
            )
            return
        
        # Processar dados e calcular GS médio
        players_data = []
        total_gs = 0
        
        for record in all_gearscores:
            # Extrair dados do registro
            if isinstance(record, dict):
                user_id = record.get('user_id', '')
                family_name = record.get('family_name', '')
                class_pvp = record.get('class_pvp', '')
                ap = record.get('ap', 0)
                aap = record.get('aap', 0)
                dp = record.get('dp', 0)
            else:
                # Ordem: id(0), user_id(1), family_name(2), character_name(3), class_pvp(4), ap(5), aap(6), dp(7), linkgear(8), updated_at(9)
                user_id = record[1] if len(record) > 1 else ''
                family_name = record[2] if len(record) > 2 else ''
                class_pvp = record[4] if len(record) > 4 else ''
                ap = record[5] if len(record) > 5 else 0
                aap = record[6] if len(record) > 6 else 0
                dp = record[7] if len(record) > 7 else 0
            
            gs = max(ap, aap) + dp
            
            # Buscar membro para verificar se ainda tem cargo
            try:
                member = interaction.guild.get_member(int(user_id))
                if not member or not has_guild_role(member):
                    continue  # Pular se não tem cargo
            except (ValueError, AttributeError):
                continue  # Pular se não conseguir verificar
            
            players_data.append({
                'user_id': user_id,
                'family_name': family_name,
                'class_pvp': class_pvp,
                'ap': ap,
                'aap': aap,
                'dp': dp,
                'gs': gs
            })
            total_gs += gs
        
        if not players_data:
            await interaction.followup.send(
                "❌ Nenhum player com cargo da guilda encontrado!",
                ephemeral=True
            )
            return
        
        # Calcular GS médio
        avg_gs = total_gs // len(players_data)
        
        # Filtrar players abaixo da média
        players_abaixo = [
            p for p in players_data
            if p['gs'] < avg_gs
        ]
        
        # Ordenar por GS (menor primeiro)
        players_abaixo.sort(key=lambda x: x['gs'])
        
        if not players_abaixo:
            await interaction.followup.send(
                f"✅ **Todos os players estão acima ou na média!**\n\n"
                f"📊 **GS Médio da Guilda:** {avg_gs}\n"
                f"👥 **Total de players:** {len(players_data)}",
                ephemeral=True
            )
            return
        
        # Criar embed
        embed = discord.Embed(
            title="📉 Players Abaixo do GS Médio",
            description=f"GS Médio da Guilda: **{avg_gs}**\n"
                        f"Total de players: **{len(players_data)}**\n"
                        f"Players abaixo da média: **{len(players_abaixo)}** ({len(players_abaixo)/len(players_data)*100:.1f}%)",
            color=discord.Color.orange(),
            timestamp=discord.utils.utcnow()
        )
        
        # Criar lista de players
        players_text = ""
        for i, player in enumerate(players_abaixo, 1):
            diff = avg_gs - player['gs']
            players_text += f"**{i}.** {player['family_name']} ({player['class_pvp']})\n"
            players_text += f"   GS: **{player['gs']}** (-{diff}) | Build: {player['ap']}/{player['aap']}/{player['dp']}\n\n"
        
        # Dividir em múltiplos campos se necessário
        if len(players_text) > 1024:
            parts = []
            current_part = ""
            
            for i, player in enumerate(players_abaixo, 1):
                diff = avg_gs - player['gs']
                line = f"**{i}.** {player['family_name']} ({player['class_pvp']})\n"
                line += f"   GS: **{player['gs']}** (-{diff}) | Build: {player['ap']}/{player['aap']}/{player['dp']}\n\n"
                
                if len(current_part + line) > 1024:
                    if current_part:
                        parts.append(current_part)
                    current_part = line
                else:
                    current_part += line
            
            if current_part:
                parts.append(current_part)
            
            # Adicionar campos
            for idx, part in enumerate(parts[:5]):  # Máximo 5 campos
                field_name = "👥 Players Abaixo da Média" if idx == 0 else f"👥 Players (cont. {idx+1})"
                embed.add_field(name=field_name, value=part[:1024], inline=False)
        else:
            embed.add_field(
                name="👥 Players Abaixo da Média",
                value=players_text,
                inline=False
            )
        
        embed.set_footer(text=f"Consultado por {interaction.user.display_name}")
        
        await interaction.followup.send(embed=embed, ephemeral=True)
        
    except Exception as e:
        import traceback
        logger.error(f"Erro ao buscar players abaixo da média: {traceback.format_exc()}")
        if interaction.response.is_done():
            await interaction.followup.send(
                f"❌ Erro ao buscar players abaixo da média: {str(e)}",
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"❌ Erro ao buscar players abaixo da média: {str(e)}",
                ephemeral=True
            )

# Comando comentado temporariamente
# @bot.tree.command(name="admin_limpar_banco", description="[ADMIN] Limpa o banco de dados (CUIDADO: Irreversível!)")
# @app_commands.describe(
#     tipo="O que deseja limpar",
#     confirmar="Digite 'CONFIRMAR' para executar (case-sensitive)"
# )
# @app_commands.choices(tipo=[
#     app_commands.Choice(name="Tudo (Gearscore + Histórico)", value="tudo"),
#     app_commands.Choice(name="Apenas Histórico", value="historico")
# ])
# # async def admin_limpar_banco(interaction: discord.Interaction, tipo: app_commands.Choice[str], confirmar: str):
#     """Limpa o banco de dados (apenas administradores)"""
#     if not interaction.user.guild_permissions.administrator:
#         await interaction.response.send_message(
#             "❌ Apenas administradores podem usar este comando!",
#             ephemeral=True
#         )
#         return
#     
#     # Verificar confirmação
#     if confirmar != "CONFIRMAR":
#         await interaction.response.send_message(
#             "❌ **Confirmação inválida!**\n\n"
#             "Para limpar o banco de dados, você deve digitar exatamente `CONFIRMAR` no parâmetro `confirmar`.\n\n"
#             "⚠️ **ATENÇÃO:** Esta ação é **IRREVERSÍVEL** e apagará todos os dados!",
#             ephemeral=True
#         )
#         return
#     
#     try:
#         await interaction.response.defer(ephemeral=True)
#         
#         if tipo.value == "tudo":
#             success, message = db.clear_all_data()
#             action = "**TODOS OS DADOS** (Gearscore + Histórico)"
#         else:
#             success, message = db.clear_history_only()
#             action = "**HISTÓRICO** (Gearscore mantido)"
#         
#         if success:
#             embed = discord.Embed(
#                 title="✅ Banco de Dados Limpo",
#                 description=f"**{action}** foram removidos com sucesso!",
#                 color=discord.Color.green(),
#                 timestamp=discord.utils.utcnow()
#             )
#             embed.add_field(name="📋 Detalhes", value=message, inline=False)
#             embed.set_footer(text=f"Limpeza executada por {interaction.user.display_name}")
#             await interaction.followup.send(embed=embed, ephemeral=True)
#         else:
#             await interaction.followup.send(
#                 f"❌ Erro ao limpar banco de dados:\n{message}",
#                 ephemeral=True
#             )
#     except Exception as e:
#         if interaction.response.is_done():
#             await interaction.followup.send(
#                 f"❌ Erro ao limpar banco de dados: {str(e)}",
#                 ephemeral=True
#             )
#         else:
#             await interaction.response.send_message(
#                 f"❌ Erro ao limpar banco de dados: {str(e)}",
#                 ephemeral=True
#             )

@bot.tree.command(name="analise_classe", description="[ADMIN] Análise completa de uma classe com relatório detalhado de todos os membros")
@app_commands.describe(
    classe="Classe a ser analisada (digite para buscar)"
)
@app_commands.autocomplete(classe=classe_autocomplete)
async def analise_classe(interaction: discord.Interaction, classe: str):
    """Análise completa de uma classe com relatório detalhado de todos os membros (apenas administradores)"""
    if not is_admin_user(interaction.user):
        await interaction.response.send_message(
            "❌ Apenas administradores podem usar este comando!",
            ephemeral=True
        )
        return
    
    if classe not in BDO_CLASSES:
        await interaction.response.send_message(
            f"❌ Classe inválida! Use `/estatisticas_classes` para ver as classes disponíveis.",
            ephemeral=True
        )
        return
    
    try:
        # Deferir resposta antes de operações que podem demorar
        await interaction.response.defer(ephemeral=True)
        
        # Buscar apenas membros que têm o cargo da guilda
        valid_user_ids = await get_guild_member_ids(interaction.guild)
        members = db.get_class_members(classe, valid_user_ids=valid_user_ids)
        
        if not members:
            await interaction.followup.send(
                f"❌ Nenhum membro encontrado com a classe {classe} (apenas membros com cargo da guilda)",
                ephemeral=True
            )
            return
        
        # Calcular médias
        total_ap = 0
        total_aap = 0
        total_dp = 0
        total_gs = 0
        
        for member in members:
            if isinstance(member, dict):
                ap = int(member.get('ap', 0) or 0)
                aap = int(member.get('aap', 0) or 0)
                dp = int(member.get('dp', 0) or 0)
            else:
                # Ordem das colunas: id(0), user_id(1), family_name(2), character_name(3), class_pvp(4), ap(5), aap(6), dp(7), linkgear(8), updated_at(9)
                ap = int(member[5] or 0) if len(member) > 5 else 0
                aap = int(member[6] or 0) if len(member) > 6 else 0
                dp = int(member[7] or 0) if len(member) > 7 else 0
            
            total_ap += ap
            total_aap += aap
            total_dp += dp
            total_gs += calculate_gs(ap, aap, dp)  # MAX(AP, AAP) + DP
        
        count = len(members)
        avg_ap = int(total_ap / count) if count > 0 else 0
        avg_aap = int(total_aap / count) if count > 0 else 0
        avg_dp = int(total_dp / count) if count > 0 else 0
        avg_gs = int(total_gs / count) if count > 0 else 0
        
        embed = discord.Embed(
            title=f"📊 Análise Detalhada - {classe}",
            color=discord.Color.gold(),
            timestamp=discord.utils.utcnow()
        )
        
        embed.add_field(name="👥 Total de Membros", value=f"**{count}**", inline=True)
        embed.add_field(name="📊 GS Médio", value=f"**{avg_gs}**", inline=True)
        embed.add_field(name="\u200b", value="\u200b", inline=True)  # Espaço vazio
        
        embed.add_field(name="⚔️ AP Médio", value=f"**{avg_ap}**", inline=True)
        embed.add_field(name="🔥 AAP Médio", value=f"**{avg_aap}**", inline=True)
        embed.add_field(name="🛡️ DP Médio", value=f"**{avg_dp}**", inline=True)
        
        # Top 5 da classe
        top_5 = members[:5]
        top_text = ""
        for i, member in enumerate(top_5, 1):
            if isinstance(member, dict):
                family_name = member.get('family_name', 'N/A')
                ap = int(member.get('ap', 0) or 0)
                aap = int(member.get('aap', 0) or 0)
                dp = int(member.get('dp', 0) or 0)
                gs = calculate_gs(ap, aap, dp)
            else:
                # Ordem das colunas: id(0), user_id(1), family_name(2), character_name(3), class_pvp(4), ap(5), aap(6), dp(7), linkgear(8), updated_at(9)
                family_name = member[2] if len(member) > 2 else 'N/A'
                ap = int(member[5] or 0) if len(member) > 5 else 0
                aap = int(member[6] or 0) if len(member) > 6 else 0
                dp = int(member[7] or 0) if len(member) > 7 else 0
                gs = calculate_gs(ap, aap, dp)
            
            medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"#{i}"
            top_text += f"{medal} **{family_name}** - {gs} GS\n"
        
        if top_text:
            embed.add_field(name="🏆 Top 5 da Classe", value=top_text, inline=False)
        
        await interaction.followup.send(embed=embed, ephemeral=True)
        
        # Criar relatório completo de todos os membros
        # Ordenar membros por GS (maior para menor)
        def get_gs_from_member(member):
            if isinstance(member, dict):
                ap = int(member.get('ap', 0) or 0)
                aap = int(member.get('aap', 0) or 0)
                dp = int(member.get('dp', 0) or 0)
            else:
                # Ordem das colunas: id(0), user_id(1), family_name(2), character_name(3), class_pvp(4), ap(5), aap(6), dp(7), linkgear(8), updated_at(9)
                ap = int(member[5] or 0) if len(member) > 5 else 0
                aap = int(member[6] or 0) if len(member) > 6 else 0
                dp = int(member[7] or 0) if len(member) > 7 else 0
            return calculate_gs(ap, aap, dp)
        
        sorted_members = sorted(members, key=get_gs_from_member, reverse=True)
        
        # Criar embeds com relatório completo
        # Dividir em múltiplos embeds se necessário (limite de 25 campos por embed)
        members_per_embed = 20  # Deixar margem para não exceder 25 campos
        
        for embed_idx in range(0, len(sorted_members), members_per_embed):
            report_embed = discord.Embed(
                title=f"📋 Relatório Completo - {classe}",
                description=f"Lista detalhada de todos os membros (ordenado por GS)",
                color=discord.Color.blue(),
                timestamp=discord.utils.utcnow()
            )
            
            if embed_idx == 0:
                report_embed.add_field(
                    name="📊 Resumo",
                    value=f"**Total de membros:** {len(sorted_members)}\n"
                          f"**GS Médio:** {avg_gs}\n"
                          f"**AP Médio:** {avg_ap} | **AAP Médio:** {avg_aap} | **DP Médio:** {avg_dp}",
                    inline=False
                )
            
            # Adicionar membros deste embed
            chunk_members = sorted_members[embed_idx:embed_idx + members_per_embed]
            
            for i, member in enumerate(chunk_members, 1):
                # Formatar dados dependendo do banco
                if isinstance(member, dict):
                    family_name = member.get('family_name', 'N/A')
                    ap = int(member.get('ap', 0) or 0)
                    aap = int(member.get('aap', 0) or 0)
                    dp = int(member.get('dp', 0) or 0)
                    linkgear = member.get('linkgear', 'N/A')
                else:
                    # Ordem das colunas: id(0), user_id(1), family_name(2), character_name(3), class_pvp(4), ap(5), aap(6), dp(7), linkgear(8), updated_at(9)
                    family_name = member[2] if len(member) > 2 else 'N/A'
                    ap = int(member[5] or 0) if len(member) > 5 else 0
                    aap = int(member[6] or 0) if len(member) > 6 else 0
                    dp = int(member[7] or 0) if len(member) > 7 else 0
                    linkgear = member[8] if len(member) > 8 else 'N/A'
                
                gs_total = calculate_gs(ap, aap, dp)
                position = embed_idx + i
                
                # Criar texto do membro
                member_info = f"**GS:** {gs_total}\n"
                member_info += f"⚔️ AP: {ap} | 🔥 AAP: {aap} | 🛡️ DP: {dp}\n"
                member_info += f"🔗 **Link Gear:** {linkgear or 'Não informado'}"
                
                # Adicionar campo (limite de 25 campos por embed do Discord)
                if len(report_embed.fields) < 25:
                    report_embed.add_field(
                        name=f"#{position} - {family_name}",
                        value=member_info,
                        inline=False
                    )
            
            # Adicionar footer com informações de paginação
            if len(sorted_members) > members_per_embed:
                total_pages = (len(sorted_members) + members_per_embed - 1) // members_per_embed
                current_page = (embed_idx // members_per_embed) + 1
                report_embed.set_footer(text=f"Página {current_page} de {total_pages} | Total: {len(sorted_members)} membros")
            else:
                report_embed.set_footer(text=f"Total: {len(sorted_members)} membros")
            
            await interaction.followup.send(embed=report_embed, ephemeral=True)
        
    except Exception as e:
        # Verificar se já respondeu
        if interaction.response.is_done():
            await interaction.followup.send(
                f"❌ Erro ao analisar classe: {str(e)}",
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"❌ Erro ao analisar classe: {str(e)}",
                ephemeral=True
            )

@bot.tree.command(name="admin_membros_sem_registro", description="[ADMIN] Lista membros com cargo da guilda que ainda não registraram gearscore")
async def admin_membros_sem_registro(interaction: discord.Interaction):
    """Lista membros com cargo da guilda que ainda não fizeram registro (apenas administradores)"""
    if not is_admin_user(interaction.user):
        await interaction.response.send_message(
            "❌ Apenas administradores podem usar este comando!",
            ephemeral=True
        )
        return
    
    try:
        await interaction.response.defer(ephemeral=True)
        
        # Buscar todos os membros com o cargo da guilda
        valid_user_ids = await get_guild_member_ids(interaction.guild)
        
        if not valid_user_ids:
            await interaction.followup.send(
                "❌ Nenhum membro com o cargo da guilda encontrado!",
                ephemeral=True
            )
            return
        
        # Buscar todos os registros do banco de dados
        all_registered = db.get_all_gearscores(valid_user_ids=valid_user_ids)
        
        # Extrair user_ids que têm registro
        registered_user_ids = set()
        for record in all_registered:
            if isinstance(record, dict):
                user_id = record.get('user_id', '')
            else:
                # Ordem das colunas: id(0), user_id(1), family_name(2), character_name(3), class_pvp(4), ap(5), aap(6), dp(7), linkgear(8), updated_at(9)
                user_id = record[1] if len(record) > 1 else ''
            
            if user_id:
                registered_user_ids.add(str(user_id))
        
        # Encontrar membros sem registro
        members_without_registry = []
        for user_id in valid_user_ids:
            if user_id not in registered_user_ids:
                member = interaction.guild.get_member(int(user_id))
                if member:
                    members_without_registry.append(member)
        
        # Criar embed
        embed = discord.Embed(
            title="📋 Membros Sem Registro",
            description=f"Membros com cargo da guilda que ainda não registraram gearscore",
            color=discord.Color.orange(),
            timestamp=discord.utils.utcnow()
        )
        
        if not members_without_registry:
            embed.add_field(
                name="✅ Todos Registrados",
                value="Todos os membros com cargo da guilda já possuem registro!",
                inline=False
            )
        else:
            # Ordenar por nome
            members_without_registry.sort(key=lambda m: m.display_name.lower())
            
            # Criar lista de membros
            members_list = ""
            for i, member in enumerate(members_without_registry, 1):
                members_list += f"{i}. {member.mention} ({member.display_name})\n"
                
                # Dividir em múltiplos campos se necessário (limite de 1024 caracteres por field)
                if len(members_list) > 900:  # Deixar margem
                    # Adicionar campo atual
                    embed.add_field(
                        name=f"🚫 Membros Sem Registro (cont.)",
                        value=members_list,
                        inline=False
                    )
                    members_list = ""
            
            # Adicionar último campo se houver conteúdo
            if members_list:
                field_name = "🚫 Membros Sem Registro" if len(embed.fields) == 0 else "🚫 Membros Sem Registro (cont.)"
                embed.add_field(
                    name=field_name,
                    value=members_list,
                    inline=False
                )
            
            embed.add_field(
                name="📊 Estatísticas",
                value=f"**Total sem registro:** {len(members_without_registry)} membro(s)\n"
                      f"**Total com registro:** {len(registered_user_ids)} membro(s)\n"
                      f"**Total de membros:** {len(valid_user_ids)} membro(s)",
                inline=False
            )
        
        embed.set_footer(text=f"Consulta executada por {interaction.user.display_name}")
        await interaction.followup.send(embed=embed, ephemeral=True)
        
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        logger.error(f"Erro ao buscar membros sem registro: {error_details}")
        
        if interaction.response.is_done():
            await interaction.followup.send(
                f"❌ Erro ao buscar membros sem registro: {str(e)}",
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"❌ Erro ao buscar membros sem registro: {str(e)}",
                ephemeral=True
            )

@bot.tree.command(name="admin_enviar_lembretes", description="[ADMIN] Envia lembretes de atualização de GS manualmente")
async def admin_enviar_lembretes(interaction: discord.Interaction):
    """Envia lembretes de atualização de GS manualmente (apenas administradores)"""
    if not is_admin_user(interaction.user):
        await interaction.response.send_message(
            "❌ Apenas administradores podem usar este comando!",
            ephemeral=True
        )
        return
    
    try:
        await interaction.response.defer(ephemeral=True)
        
        reminders_sent, errors = await check_gs_update_reminders(interaction.guild)
        
        embed = discord.Embed(
            title="📤 Lembretes de Atualização de GS Enviados",
            description=f"Foram verificados os membros que não atualizaram há mais de **{GS_UPDATE_REMINDER_DAYS} dias**.",
            color=discord.Color.green() if errors == 0 else discord.Color.orange(),
            timestamp=discord.utils.utcnow()
        )
        
        embed.add_field(name="✅ Lembretes Enviados", value=f"**{reminders_sent}**", inline=True)
        embed.add_field(name="❌ Erros", value=f"**{errors}**", inline=True)
        embed.add_field(name="📅 Dias sem atualizar", value=f"**{GS_UPDATE_REMINDER_DAYS}+**", inline=True)
        
        embed.set_footer(text=f"Executado por {interaction.user.display_name}")
        await interaction.followup.send(embed=embed, ephemeral=True)
        
        logger.info(f"Lembretes de GS enviados manualmente por {interaction.user.display_name} (ID: {interaction.user.id}): {reminders_sent} enviados, {errors} erros")
        
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        logger.error(f"Erro ao enviar lembretes manualmente: {error_details}")
        
        if interaction.response.is_done():
            await interaction.followup.send(
                f"❌ Erro ao enviar lembretes: {str(e)}",
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"❌ Erro ao enviar lembretes: {str(e)}",
                ephemeral=True
            )

@bot.tree.command(name="admin_gs_desatualizados", description="[ADMIN] Lista membros com GS desatualizado")
@app_commands.describe(
    dias="Número de dias sem atualizar (padrão: configuração do bot)"
)
async def admin_gs_desatualizados(interaction: discord.Interaction, dias: int = None):
    """Lista membros que não atualizaram GS há X dias (apenas administradores)"""
    if not is_admin_user(interaction.user):
        await interaction.response.send_message(
            "❌ Apenas administradores podem usar este comando!",
            ephemeral=True
        )
        return
    
    if dias is None:
        dias = GS_UPDATE_REMINDER_DAYS
    
    try:
        await interaction.response.defer(ephemeral=True)
        
        # Buscar todos os membros com cargo da guilda
        guild_member_ids = await get_guild_member_ids(interaction.guild)
        
        if not guild_member_ids:
            await interaction.followup.send(
                "❌ Nenhum membro com o cargo da guilda encontrado!",
                ephemeral=True
            )
            return
        
        # Buscar todos os registros do banco
        all_registered = db.get_all_gearscores(valid_user_ids=guild_member_ids)
        
        # Data limite para considerar desatualizado
        now = datetime.now()
        limit_date = now - timedelta(days=dias)
        
        outdated_members = []
        
        for record in all_registered:
            try:
                # Extrair dados do registro
                if isinstance(record, dict):
                    user_id = record.get('user_id', '')
                    family_name = record.get('family_name', 'N/A')
                    class_pvp = record.get('class_pvp', 'N/A')
                    ap = record.get('ap', 0)
                    aap = record.get('aap', 0)
                    dp = record.get('dp', 0)
                    updated_at = record.get('updated_at')
                else:
                    # Ordem das colunas: id(0), user_id(1), family_name(2), character_name(3), class_pvp(4), ap(5), aap(6), dp(7), linkgear(8), updated_at(9)
                    user_id = str(record[1]) if len(record) > 1 else ''
                    family_name = record[2] if len(record) > 2 else 'N/A'
                    class_pvp = record[4] if len(record) > 4 else 'N/A'
                    ap = record[5] if len(record) > 5 else 0
                    aap = record[6] if len(record) > 6 else 0
                    dp = record[7] if len(record) > 7 else 0
                    updated_at = record[9] if len(record) > 9 else None
                
                if not user_id or not updated_at:
                    continue
                
                # Converter updated_at para datetime
                if isinstance(updated_at, str):
                    for fmt in ['%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M:%S.%f', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%dT%H:%M:%S.%f']:
                        try:
                            updated_datetime = datetime.strptime(updated_at.split('+')[0].split('Z')[0], fmt)
                            break
                        except:
                            continue
                    else:
                        continue
                elif hasattr(updated_at, 'replace'):
                    updated_datetime = updated_at.replace(tzinfo=None) if updated_at.tzinfo else updated_at
                else:
                    continue
                
                # Verificar se está desatualizado
                if updated_datetime >= limit_date:
                    continue
                
                days_since_update = (now - updated_datetime).days
                
                member = interaction.guild.get_member(int(user_id))
                if not member or not has_guild_role(member):
                    continue
                
                gs_total = calculate_gs(ap, aap, dp)
                outdated_members.append({
                    'member': member,
                    'family_name': family_name,
                    'class_pvp': class_pvp,
                    'gs': gs_total,
                    'days': days_since_update,
                    'last_update': updated_datetime
                })
                
            except Exception as e:
                continue
        
        # Ordenar por dias (mais tempo sem atualizar primeiro)
        outdated_members.sort(key=lambda x: x['days'], reverse=True)
        
        # Criar embed
        embed = discord.Embed(
            title=f"📋 Membros com GS Desatualizado ({dias}+ dias)",
            description=f"Membros que não atualizaram o gearscore há mais de **{dias} dias**.",
            color=discord.Color.orange(),
            timestamp=discord.utils.utcnow()
        )
        
        if not outdated_members:
            embed.add_field(
                name="✅ Todos Atualizados",
                value=f"Nenhum membro está com GS desatualizado há mais de {dias} dias!",
                inline=False
            )
        else:
            # Criar lista de membros (limitada para caber no embed)
            members_list = ""
            for i, m in enumerate(outdated_members[:20], 1):
                members_list += f"**{i}.** {m['member'].mention} - {m['family_name']} ({m['class_pvp']}) - **{m['gs']}** GS - {m['days']} dias\n"
            
            if len(outdated_members) > 20:
                members_list += f"\n... e mais {len(outdated_members) - 20} membro(s)"
            
            embed.add_field(
                name=f"🚫 Membros Desatualizados ({len(outdated_members)})",
                value=members_list[:1024],
                inline=False
            )
            
            embed.add_field(
                name="📊 Estatísticas",
                value=f"**Total desatualizados:** {len(outdated_members)}\n"
                      f"**Total com registro:** {len(all_registered)}\n"
                      f"**Maior tempo sem atualizar:** {outdated_members[0]['days']} dias" if outdated_members else "N/A",
                inline=False
            )
        
        embed.set_footer(text=f"Consulta executada por {interaction.user.display_name}")
        await interaction.followup.send(embed=embed, ephemeral=True)
        
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        logger.error(f"Erro ao buscar GS desatualizados: {error_details}")
        
        if interaction.response.is_done():
            await interaction.followup.send(
                f"❌ Erro ao buscar membros desatualizados: {str(e)}",
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"❌ Erro ao buscar membros desatualizados: {str(e)}",
                ephemeral=True
            )

# Função helper para obter o registro mais recente de cada usuário (um por pessoa)
def get_latest_record_per_user(records) -> dict:
    """Retorna {user_id: dados} usando apenas o registro atualizado mais recentemente de cada usuário"""
    latest = {}
    for record in records:
        if isinstance(record, dict):
            data = {
                'user_id': str(record.get('user_id', '')),
                'family_name': record.get('family_name', 'N/A'),
                'class_pvp': record.get('class_pvp', 'N/A'),
                'ap': record.get('ap', 0) or 0,
                'aap': record.get('aap', 0) or 0,
                'dp': record.get('dp', 0) or 0,
                'updated_at': record.get('updated_at')
            }
        else:
            # Ordem: id(0), user_id(1), family_name(2), character_name(3), class_pvp(4), ap(5), aap(6), dp(7), linkgear(8), updated_at(9)
            data = {
                'user_id': str(record[1]),
                'family_name': record[2] or 'N/A',
                'class_pvp': record[4] or 'N/A',
                'ap': record[5] or 0,
                'aap': record[6] or 0,
                'dp': record[7] or 0,
                'updated_at': record[9] if len(record) > 9 else None
            }
        
        updated_at = data['updated_at']
        if isinstance(updated_at, str):
            for fmt in ['%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M:%S.%f', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%dT%H:%M:%S.%f']:
                try:
                    updated_at = datetime.strptime(updated_at.split('+')[0].split('Z')[0], fmt)
                    break
                except ValueError:
                    continue
            else:
                updated_at = None
        elif updated_at is not None and getattr(updated_at, 'tzinfo', None):
            updated_at = updated_at.replace(tzinfo=None)
        data['updated_at'] = updated_at
        
        if not data['user_id']:
            continue
        
        current = latest.get(data['user_id'])
        if current is None or (updated_at and (current['updated_at'] is None or updated_at > current['updated_at'])):
            latest[data['user_id']] = data
    
    return latest

@bot.tree.command(name="admin_cobrar_atualizacao", description="[ADMIN] Cobra via DM (urgente) quem não atualizou o GS nos últimos dias")
@app_commands.describe(
    dias="Quantidade de dias sem atualizar para cobrar (padrão: 3)"
)
async def admin_cobrar_atualizacao(interaction: discord.Interaction, dias: int = 3):
    """Envia DM urgente da staff para membros que não atualizaram o GS nos últimos X dias"""
    if not is_admin_user(interaction.user):
        await interaction.response.send_message(
            "❌ Apenas administradores podem usar este comando!",
            ephemeral=True
        )
        return
    
    if dias < 1:
        await interaction.response.send_message(
            "❌ O número de dias deve ser maior ou igual a 1!",
            ephemeral=True
        )
        return
    
    if not interaction.guild:
        await interaction.response.send_message(
            "❌ Este comando só pode ser usado em um servidor!",
            ephemeral=True
        )
        return
    
    try:
        await interaction.response.defer(ephemeral=True)
        
        guild_member_ids = await get_guild_member_ids(interaction.guild)
        if not guild_member_ids:
            await interaction.followup.send(
                "❌ Nenhum membro com o cargo da guilda encontrado!",
                ephemeral=True
            )
            return
        
        latest_records = get_latest_record_per_user(db.get_all_gearscores(valid_user_ids=guild_member_ids))
        
        now = datetime.now()
        limit_date = now - timedelta(days=dias)
        
        outdated = []
        for user_id, data in latest_records.items():
            if data['updated_at'] and data['updated_at'] >= limit_date:
                continue
            member = interaction.guild.get_member(int(user_id))
            if not member or not has_guild_role(member):
                continue
            outdated.append((member, data))
        
        if not outdated:
            await interaction.followup.send(
                f"✅ Todos os membros registrados atualizaram o GS nos últimos **{dias} dia(s)**!",
                ephemeral=True
            )
            return
        
        sent = []
        failed = []
        
        for member, data in outdated:
            days_since_update = (now - data['updated_at']).days if data['updated_at'] else None
            gs_total = calculate_gs(data['ap'], data['aap'], data['dp'])
            
            embed = discord.Embed(
                title="🚨 URGENTE: Atualize seu Gearscore",
                description=(
                    f"Olá **{member.display_name}**!\n\n"
                    f"A **staff da guilda** está pedindo, com **urgência**, que todos atualizem o gearscore.\n"
                    f"Seu GS não foi atualizado nos últimos **{dias} dia(s)**"
                    + (f" (última atualização há **{days_since_update} dia(s)**)" if days_since_update is not None else "")
                    + ".\n\n"
                    f"📋 **Você pode usar `/atualizar` aqui mesmo, no privado do bot!**\n"
                    f"Basta digitar `/atualizar` nesta conversa e preencher AP, AAP e DP (o link do gear é opcional).\n\n"
                    f"⚠️ Mesmo que você não tenha evoluído nada, atualize mesmo assim. "
                    f"Isso é necessário para o **controle interno da guilda**."
                ),
                color=discord.Color.red(),
                timestamp=discord.utils.utcnow()
            )
            embed.add_field(name="👤 Família", value=data['family_name'], inline=True)
            embed.add_field(name="⚔️ Classe", value=data['class_pvp'], inline=True)
            embed.add_field(name="📊 GS Atual", value=f"**{gs_total}**", inline=True)
            if data['updated_at']:
                embed.set_footer(text=f"Última atualização: {data['updated_at'].strftime('%d/%m/%Y às %H:%M')}")
            
            try:
                await member.send(embed=embed)
                sent.append((member, data))
            except discord.Forbidden:
                failed.append((member, data))
            except Exception as e:
                logger.error(f"Erro ao enviar cobrança de GS para {member.display_name} (ID: {member.id}): {e}")
                failed.append((member, data))
            
            # Evitar rate limit do Discord ao enviar muitas DMs
            await asyncio.sleep(1)
        
        embed = discord.Embed(
            title=f"🚨 Cobrança de Atualização de GS ({dias}+ dias)",
            description=f"Membros com cargo da guilda que **não atualizaram** o GS nos últimos **{dias} dia(s)** foram cobrados via DM.",
            color=discord.Color.green() if not failed else discord.Color.orange(),
            timestamp=discord.utils.utcnow()
        )
        embed.add_field(name="📋 Desatualizados", value=f"**{len(outdated)}**", inline=True)
        embed.add_field(name="✅ DMs Enviadas", value=f"**{len(sent)}**", inline=True)
        embed.add_field(name="❌ DM Fechada/Erro", value=f"**{len(failed)}**", inline=True)
        
        if failed:
            failed_text = "\n".join(f"{m.mention} - {d['family_name']}" for m, d in failed)
            if len(failed_text) > 1024:
                failed_text = failed_text[:990].rsplit("\n", 1)[0] + f"\n... (total: {len(failed)})"
            embed.add_field(
                name="🚫 Não receberam a DM (cobrar no canal)",
                value=failed_text,
                inline=False
            )
        
        embed.set_footer(text=f"Executado por {interaction.user.display_name}")
        await interaction.followup.send(embed=embed, ephemeral=True)
        
        logger.info(f"Cobrança de GS ({dias}+ dias) por {interaction.user.display_name} (ID: {interaction.user.id}): {len(sent)} enviadas, {len(failed)} falhas")
        
    except Exception as e:
        import traceback
        logger.error(f"Erro ao cobrar atualização de GS: {traceback.format_exc()}")
        if interaction.response.is_done():
            await interaction.followup.send(f"❌ Erro ao cobrar atualização: {str(e)}", ephemeral=True)
        else:
            await interaction.response.send_message(f"❌ Erro ao cobrar atualização: {str(e)}", ephemeral=True)

@bot.tree.command(name="media_gs", description="[ADMIN] Mostra a média de GS da guilda")
async def media_gs(interaction: discord.Interaction):
    """Mostra a média de GS da guilda (um registro por membro, o mais recente)"""
    if not is_admin_user(interaction.user):
        await interaction.response.send_message(
            "❌ Apenas administradores podem usar este comando!",
            ephemeral=True
        )
        return
    
    if not interaction.guild:
        await interaction.response.send_message(
            "❌ Este comando só pode ser usado em um servidor!",
            ephemeral=True
        )
        return
    
    try:
        await interaction.response.defer(ephemeral=True)
        
        guild_member_ids = await get_guild_member_ids(interaction.guild)
        if not guild_member_ids:
            await interaction.followup.send(
                "❌ Nenhum membro com o cargo da guilda encontrado!",
                ephemeral=True
            )
            return
        
        latest_records = get_latest_record_per_user(db.get_all_gearscores(valid_user_ids=guild_member_ids))
        
        players = []
        for user_id, data in latest_records.items():
            member = interaction.guild.get_member(int(user_id))
            if not member or not has_guild_role(member):
                continue
            data['gs'] = calculate_gs(data['ap'], data['aap'], data['dp'])
            players.append(data)
        
        if not players:
            await interaction.followup.send(
                "❌ Nenhum gearscore registrado entre os membros da guilda!",
                ephemeral=True
            )
            return
        
        total = len(players)
        gs_values = sorted(p['gs'] for p in players)
        avg_gs = sum(gs_values) / total
        mid = total // 2
        median_gs = gs_values[mid] if total % 2 else (gs_values[mid - 1] + gs_values[mid]) / 2
        avg_ap = sum(p['ap'] for p in players) / total
        avg_aap = sum(p['aap'] for p in players) / total
        avg_dp = sum(p['dp'] for p in players) / total
        
        top = max(players, key=lambda p: p['gs'])
        bottom = min(players, key=lambda p: p['gs'])
        above = sum(1 for g in gs_values if g >= avg_gs)
        
        embed = discord.Embed(
            title="📊 Média de GS da Guilda",
            description=f"## {avg_gs:.1f} GS\nBaseado em **{total}** membros com cargo da guilda e GS registrado.",
            color=discord.Color.blue(),
            timestamp=discord.utils.utcnow()
        )
        embed.add_field(name="📈 Mediana", value=f"**{median_gs:.0f}**", inline=True)
        embed.add_field(name="🔝 Maior GS", value=f"**{top['gs']}** ({top['family_name']})", inline=True)
        embed.add_field(name="🔻 Menor GS", value=f"**{bottom['gs']}** ({bottom['family_name']})", inline=True)
        embed.add_field(name="⚔️ AP Médio", value=f"**{avg_ap:.1f}**", inline=True)
        embed.add_field(name="🔥 AAP Médio", value=f"**{avg_aap:.1f}**", inline=True)
        embed.add_field(name="🛡️ DP Médio", value=f"**{avg_dp:.1f}**", inline=True)
        embed.add_field(
            name="👥 Distribuição",
            value=f"Na média ou acima: **{above}** | Abaixo da média: **{total - above}**",
            inline=False
        )
        
        # Média por classe
        classes = {}
        for p in players:
            classes.setdefault(p['class_pvp'], []).append(p['gs'])
        class_lines = [
            f"**{name}**: {sum(v) / len(v):.0f} GS ({len(v)})"
            for name, v in sorted(classes.items(), key=lambda kv: sum(kv[1]) / len(kv[1]), reverse=True)
        ]
        class_text = "\n".join(class_lines)
        if len(class_text) > 1024:
            class_text = class_text[:990].rsplit("\n", 1)[0] + "\n..."
        embed.add_field(name="🎭 Média por Classe (membros)", value=class_text, inline=False)
        
        embed.set_footer(text=f"Consultado por {interaction.user.display_name}")
        await interaction.followup.send(embed=embed, ephemeral=True)
        
    except Exception as e:
        import traceback
        logger.error(f"Erro ao calcular média de GS: {traceback.format_exc()}")
        if interaction.response.is_done():
            await interaction.followup.send(f"❌ Erro ao calcular média de GS: {str(e)}", ephemeral=True)
        else:
            await interaction.response.send_message(f"❌ Erro ao calcular média de GS: {str(e)}", ephemeral=True)

@bot.tree.command(name="admin_limpar_inativos", description="[ADMIN] Apaga o registro de quem não atualizou o GS nos últimos dias")
@app_commands.describe(
    dias="Quantidade de dias sem atualizar para apagar o registro (padrão: 20)",
    confirmar="Digite 'CONFIRMAR' para apagar. Sem isso, só mostra quem seria apagado"
)
async def admin_limpar_inativos(interaction: discord.Interaction, dias: int = 20, confirmar: str = ""):
    """Apaga o registro de GS (e histórico) dos membros da guilda que não atualizaram nos últimos X dias"""
    if not is_admin_user(interaction.user):
        await interaction.response.send_message(
            "❌ Apenas administradores podem usar este comando!",
            ephemeral=True
        )
        return

    if dias < 1:
        await interaction.response.send_message(
            "❌ O número de dias deve ser maior ou igual a 1!",
            ephemeral=True
        )
        return

    if not interaction.guild:
        await interaction.response.send_message(
            "❌ Este comando só pode ser usado em um servidor!",
            ephemeral=True
        )
        return

    try:
        await interaction.response.defer(ephemeral=True)

        guild_member_ids = await get_guild_member_ids(interaction.guild)
        if not guild_member_ids:
            await interaction.followup.send(
                "❌ Nenhum membro com o cargo da guilda encontrado!",
                ephemeral=True
            )
            return

        latest_records = get_latest_record_per_user(db.get_all_gearscores(valid_user_ids=guild_member_ids))

        now = datetime.now()
        limit_date = now - timedelta(days=dias)

        inactive = []
        for user_id, data in latest_records.items():
            # Sem data de atualização não dá para saber se está inativo
            if not data['updated_at'] or data['updated_at'] >= limit_date:
                continue
            member = interaction.guild.get_member(int(user_id))
            if not member or not has_guild_role(member):
                continue
            inactive.append((member, data))

        if not inactive:
            await interaction.followup.send(
                f"✅ Nenhum membro está há mais de **{dias} dia(s)** sem atualizar o GS!",
                ephemeral=True
            )
            return

        inactive.sort(key=lambda item: item[1]['updated_at'])

        def format_list(items):
            text = "\n".join(
                f"{m.mention} - {d['family_name']} ({(now - d['updated_at']).days} dias)"
                for m, d in items
            )
            if len(text) > 1024:
                text = text[:990].rsplit("\n", 1)[0] + f"\n... (total: {len(items)})"
            return text

        if confirmar != "CONFIRMAR":
            embed = discord.Embed(
                title=f"🔍 Prévia: registros com {dias}+ dias sem atualizar",
                description=(
                    f"**{len(inactive)}** membro(s) teriam o registro **e o histórico** apagados.\n\n"
                    f"Para executar, rode de novo com `confirmar: CONFIRMAR`.\n"
                    f"⚠️ Esta ação é **irreversível**. Depois disso eles precisam fazer `/registro` de novo."
                ),
                color=discord.Color.orange(),
                timestamp=discord.utils.utcnow()
            )
            embed.add_field(name="👥 Seriam apagados", value=format_list(inactive), inline=False)
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        deleted = []
        failed = []
        for member, data in inactive:
            success, message = db.delete_user_gearscore(str(member.id))
            if success:
                deleted.append((member, data))
                await update_registration_roles(member, False)
            else:
                failed.append((member, data))
                logger.error(f"Erro ao apagar registro inativo de {member.display_name} (ID: {member.id}): {message}")

        embed = discord.Embed(
            title=f"🧹 Limpeza de inativos ({dias}+ dias)",
            description=f"**{len(deleted)}** registro(s) apagado(s). Esses membros agora precisam fazer `/registro` de novo.",
            color=discord.Color.green() if not failed else discord.Color.orange(),
            timestamp=discord.utils.utcnow()
        )
        if deleted:
            embed.add_field(name="🗑️ Apagados", value=format_list(deleted), inline=False)
        if failed:
            embed.add_field(name="❌ Falharam", value=format_list(failed), inline=False)
        embed.set_footer(text=f"Executado por {interaction.user.display_name}")
        await interaction.followup.send(embed=embed, ephemeral=True)

        logger.info(f"Limpeza de inativos ({dias}+ dias) por {interaction.user.display_name} (ID: {interaction.user.id}): {len(deleted)} apagados, {len(failed)} falhas")

    except Exception as e:
        import traceback
        logger.error(f"Erro ao limpar inativos: {traceback.format_exc()}")
        if interaction.response.is_done():
            await interaction.followup.send(f"❌ Erro ao limpar inativos: {str(e)}", ephemeral=True)
        else:
            await interaction.response.send_message(f"❌ Erro ao limpar inativos: {str(e)}", ephemeral=True)

# ==================== VERIFICAÇÃO DE QUEM SAIU DA GUILDA NO JOGO ====================

GUILD_PROFILE_URL = "https://www.sa.playblackdesert.com/pt-BR/Adventure/Guild/GuildProfile"

async def fetch_bdo_guild_families(session: aiohttp.ClientSession, guild_name: str) -> set:
    """
    Busca os nomes de família da guilda no perfil público do site oficial.
    Lança ValueError se a lista não puder ser lida por completo (evita remover cargos por engano).
    """
    params = {"guildName": guild_name, "region": BDO_GUILD_REGION}
    async with session.get(GUILD_PROFILE_URL, params=params, timeout=aiohttp.ClientTimeout(total=30)) as resp:
        if resp.status != 200:
            raise ValueError(f"site respondeu HTTP {resp.status}")
        page = await resp.text()

    # A lista de membros fica em "adventure_list_table"; antes dela só aparece o mestre
    start = page.find("adventure_list_table")
    if start == -1:
        raise ValueError("lista de membros não encontrada (guilda privada ou o site mudou)")

    families = {
        html.unescape(name).strip()
        for name in re.findall(r'profileTarget=[^"]*"[^>]*>([^<]+)</a>', page[start:])
    }
    families.discard("")
    if not families:
        raise ValueError("nenhum membro encontrado na página")

    # Conferir com o total de membros que o próprio site mostra
    total = re.search(r'<span class="title">Membros</span>\s*<span class="desc">\s*<span>\s*<em>(\d+)</em>', page)
    if total and int(total.group(1)) != len(families):
        raise ValueError(f"o site mostra {total.group(1)} membros mas só {len(families)} foram lidos")

    return families

def member_name_tokens(member: discord.Member, registered_family: str = None) -> set:
    """Nomes possíveis do membro (família registrada, apelido, nome global e usuário), em minúsculas"""
    tokens = set()
    for name in (registered_family, member.nick, member.global_name, member.name):
        if not name:
            continue
        tokens.add(name.strip().lower())
        # Apelidos tipo "Knowles | Musa" ou "[MOUZ] Knowles": considerar cada palavra
        tokens.update(word.lower() for word in re.findall(r'[A-Za-z0-9]+', name))
    return tokens

def has_role_above_member_role(member: discord.Member, member_role: discord.Role) -> bool:
    """True se o membro tem algum cargo acima do cargo Membro na hierarquia (Staff, Officer, etc.)"""
    ignored = {REGISTERED_ROLE_ID, UNREGISTERED_ROLE_ID, FRIEND_ROLE_ID}
    return any(role > member_role and role.id not in ignored for role in member.roles)

guild_check_lock = asyncio.Lock()

# Botões dos cards de aprovação: "gdep:<acao>:<user_id>" (tratados em on_interaction, sobrevivem a reinícios)
GUILD_DEPARTURE_PREFIX = "gdep:"

def departure_card_view(user_id: int) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(label="Saiu - dar Amigo", emoji="👋", style=discord.ButtonStyle.danger, custom_id=f"{GUILD_DEPARTURE_PREFIX}approve:{user_id}"))
    view.add_item(discord.ui.Button(label="Não saiu", emoji="✋", style=discord.ButtonStyle.secondary, custom_id=f"{GUILD_DEPARTURE_PREFIX}deny:{user_id}"))
    return view

async def get_pending_departure_ids(channel: discord.TextChannel) -> set:
    """IDs dos membros que já têm card aguardando aprovação no canal"""
    pending = set()
    async for message in channel.history(limit=500):
        if message.author.id != bot.user.id:
            continue
        for row in message.components:
            for item in getattr(row, 'children', []):
                custom_id = getattr(item, 'custom_id', None) or ''
                if custom_id.startswith(f"{GUILD_DEPARTURE_PREFIX}approve:"):
                    pending.add(int(custom_id.rsplit(':', 1)[1]))
    return pending

async def run_guild_departure_check(guild: discord.Guild, channel: discord.TextChannel) -> dict:
    """Compara quem tem o cargo Membro com as guildas do jogo e posta um card de aprovação para quem não está em nenhuma"""
    member_role = guild.get_role(GUILD_MEMBER_ROLE_ID)
    if not member_role or not guild.get_role(FRIEND_ROLE_ID):
        raise ValueError("cargo Membro ou Amigo não encontrado no servidor")
    
    game_families = set()
    async with aiohttp.ClientSession(headers={"User-Agent": "Mozilla/5.0"}) as session:
        for guild_name in BDO_GUILD_NAMES:
            try:
                families = await fetch_bdo_guild_families(session, guild_name)
            except Exception as e:
                raise ValueError(f"não consegui ler a guilda {guild_name}: {e}. Nenhum card foi criado.")
            game_families.update(f.lower() for f in families)
    
    latest_records = get_latest_record_per_user(db.get_all_gearscores(valid_user_ids={str(m.id) for m in member_role.members}))
    pending_ids = await get_pending_departure_ids(channel)
    kept_ids = db.get_guild_departure_keep_ids()
    
    result = {'game_families': len(game_families), 'new': 0, 'pending': 0, 'kept': 0, 'skipped_high_role': 0}
    
    for member in member_role.members:
        if member.bot:
            continue
        if has_role_above_member_role(member, member_role):
            result['skipped_high_role'] += 1
            continue
        
        registered_family = latest_records.get(str(member.id), {}).get('family_name')
        if member_name_tokens(member, registered_family) & game_families:
            continue
        
        if member.id in pending_ids:
            result['pending'] += 1
            continue
        
        if str(member.id) in kept_ids:
            result['kept'] += 1
            continue
        
        embed = discord.Embed(
            title="❓ Saiu da guilda?",
            description=(
                f"{member.mention} tem o cargo <@&{GUILD_MEMBER_ROLE_ID}> mas **não foi encontrado** "
                f"nas guildas **{' + '.join(BDO_GUILD_NAMES)}** do jogo."
            ),
            color=discord.Color.orange(),
            timestamp=discord.utils.utcnow()
        )
        embed.add_field(name="👤 Família (/registro)", value=registered_family or "Sem registro", inline=True)
        embed.add_field(name="✏️ Apelido no Discord", value=member.display_name, inline=True)
        embed.add_field(name="🆔 Usuário", value=member.name, inline=True)
        embed.set_footer(text=f"Aprovar: perde o cargo de membro e recebe Amigo | ID: {member.id}")
        await channel.send(embed=embed, view=departure_card_view(member.id))
        result['new'] += 1
        await asyncio.sleep(0.5)
    
    return result

async def handle_departure_card(interaction: discord.Interaction, action: str, user_id: int):
    """Trata o clique em Saiu / Não saiu de um card de aprovação"""
    if not interaction.guild or not is_admin_user(interaction.user):
        await interaction.response.send_message("❌ Apenas administradores podem aprovar!", ephemeral=True)
        return
    
    embed = interaction.message.embeds[0] if interaction.message.embeds else discord.Embed(title="Saiu da guilda?")
    member = interaction.guild.get_member(user_id)
    
    if action == "deny":
        db.add_guild_departure_keep(user_id, interaction.user.id)
        embed.title = "✋ Mantido na guilda"
        embed.color = discord.Color.light_grey()
        embed.add_field(
            name="Decisão",
            value=f"Mantido por {interaction.user.mention}. Não aparece mais nas verificações (para voltar: `/admin_guilda_reverificar`).",
            inline=False
        )
        logger.info(f"Saída da guilda negada por {interaction.user.display_name} (ID: {interaction.user.id}) para user_id {user_id}")
    elif not member:
        embed.title = "🚪 Não está mais no servidor"
        embed.color = discord.Color.light_grey()
        embed.add_field(name="Decisão", value=f"Fechado por {interaction.user.mention} (membro saiu do Discord)", inline=False)
    else:
        member_role = interaction.guild.get_role(GUILD_MEMBER_ROLE_ID)
        friend_role = interaction.guild.get_role(FRIEND_ROLE_ID)
        if not member_role or not friend_role:
            await interaction.response.send_message("❌ Cargo Membro ou Amigo não encontrado no servidor!", ephemeral=True)
            return
        reason = f"Saiu da guilda no jogo (aprovado por {interaction.user.display_name})"
        try:
            await member.add_roles(friend_role, reason=reason)
            if member_role in member.roles:
                await member.remove_roles(member_role, reason=reason)
        except (discord.Forbidden, discord.HTTPException) as e:
            logger.error(f"Erro ao passar {member.display_name} (ID: {member.id}) para Amigo: {e}")
            await interaction.response.send_message(
                f"❌ Não consegui alterar os cargos de {member.mention} (o cargo do bot precisa estar acima de Membro e Amigo): {e}",
                ephemeral=True
            )
            return
        embed.title = "👋 Saiu da guilda"
        embed.color = discord.Color.red()
        embed.add_field(name="Decisão", value=f"Aprovado por {interaction.user.mention}: agora é <@&{FRIEND_ROLE_ID}>", inline=False)
        logger.info(f"{member.display_name} (ID: {member.id}) passou para Amigo, aprovado por {interaction.user.display_name} (ID: {interaction.user.id})")
    
    await interaction.response.edit_message(embed=embed, view=None)

@bot.listen('on_interaction')
async def on_departure_card_interaction(interaction: discord.Interaction):
    if interaction.type != discord.InteractionType.component:
        return
    custom_id = (interaction.data or {}).get('custom_id', '')
    if not custom_id.startswith(GUILD_DEPARTURE_PREFIX):
        return
    try:
        action, user_id = custom_id[len(GUILD_DEPARTURE_PREFIX):].split(':')
        await handle_departure_card(interaction, action, int(user_id))
    except Exception as e:
        import traceback
        logger.error(f"Erro no card de saída da guilda: {traceback.format_exc()}")
        if not interaction.response.is_done():
            await interaction.response.send_message(f"❌ Erro: {e}", ephemeral=True)

class GuildCheckPanelView(discord.ui.View):
    """Painel fixo (sobrevive a reinícios do bot) com o botão de verificação"""
    def __init__(self):
        super().__init__(timeout=None)
    
    @discord.ui.button(label="Verificar quem saiu", emoji="🔄", style=discord.ButtonStyle.primary, custom_id="guild_departure_check")
    async def check(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild or not is_admin_user(interaction.user):
            await interaction.response.send_message("❌ Apenas administradores podem usar este painel!", ephemeral=True)
            return
        
        if guild_check_lock.locked():
            await interaction.response.send_message("⏳ Já tem uma verificação rodando, aguarde.", ephemeral=True)
            return
        
        await interaction.response.defer(ephemeral=True, thinking=True)
        async with guild_check_lock:
            try:
                channel = bot.get_channel(GUILD_CHECK_CHANNEL_ID) or await bot.fetch_channel(GUILD_CHECK_CHANNEL_ID)
                result = await run_guild_departure_check(interaction.guild, channel)
            except ValueError as e:
                await interaction.followup.send(f"❌ {e}", ephemeral=True)
                return
            except Exception as e:
                import traceback
                logger.error(f"Erro na verificação de saída da guilda: {traceback.format_exc()}")
                await interaction.followup.send(f"❌ Erro na verificação: {e}", ephemeral=True)
                return
        
        logger.info(f"Verificação de saída da guilda por {interaction.user.display_name} (ID: {interaction.user.id}): {result}")
        await interaction.followup.send(
            f"✅ Verificação concluída ({result['game_families']} famílias no jogo).\n"
            f"• **{result['new']}** novo(s) card(s) de aprovação em <#{GUILD_CHECK_CHANNEL_ID}>\n"
            f"• **{result['pending']}** já estavam aguardando aprovação\n"
            f"• **{result['kept']}** ignorado(s) por já terem sido marcados como \"Não saiu\"\n"
            f"• **{result['skipped_high_role']}** ignorado(s) por ter cargo acima de Membro",
            ephemeral=True
        )

@bot.tree.command(name="admin_painel_guilda", description="[ADMIN] Cria o painel para verificar quem saiu da guilda no jogo")
async def admin_painel_guilda(interaction: discord.Interaction):
    if not is_admin_user(interaction.user):
        await interaction.response.send_message("❌ Apenas administradores podem usar este comando!", ephemeral=True)
        return
    
    if not interaction.guild:
        await interaction.response.send_message("❌ Este comando só pode ser usado em um servidor!", ephemeral=True)
        return
    
    embed = discord.Embed(
        title="🛡️ Painel de Membros da Guilda",
        description=(
            f"Clique em **Verificar quem saiu** para comparar o Discord com as guildas "
            f"**{' + '.join(BDO_GUILD_NAMES)}** no site oficial do Black Desert.\n\n"
            f"• Só é verificado quem tem o cargo <@&{GUILD_MEMBER_ROLE_ID}> e **nenhum cargo acima** (Staff, Officer etc. são ignorados).\n"
            f"• A comparação usa a família do `/registro` e o apelido do Discord.\n"
            f"• Para cada um não encontrado aparece um card aqui: **Saiu** tira o cargo de membro e dá <@&{FRIEND_ROLE_ID}>, **Não saiu** mantém como está e o membro não aparece mais nas próximas verificações (use `/admin_guilda_reverificar` para voltar a verificar).\n"
            f"• Se o site falhar, nenhum card é criado."
        ),
        color=discord.Color.blurple()
    )
    try:
        channel = bot.get_channel(GUILD_CHECK_CHANNEL_ID) or await bot.fetch_channel(GUILD_CHECK_CHANNEL_ID)
        await channel.send(embed=embed, view=GuildCheckPanelView())
    except Exception as e:
        await interaction.response.send_message(f"❌ Não consegui postar o painel em <#{GUILD_CHECK_CHANNEL_ID}>: {e}", ephemeral=True)
        return
    await interaction.response.send_message(f"✅ Painel criado em <#{GUILD_CHECK_CHANNEL_ID}>!", ephemeral=True)
@bot.tree.command(name="admin_guilda_reverificar", description="[ADMIN] Volta a verificar um membro que foi marcado como \"Não saiu\"")
@app_commands.describe(usuario="Membro marcado como \"Não saiu\" no painel da guilda")
async def admin_guilda_reverificar(interaction: discord.Interaction, usuario: discord.Member):
    if not is_admin_user(interaction.user):
        await interaction.response.send_message("❌ Apenas administradores podem usar este comando!", ephemeral=True)
        return
    
    if db.remove_guild_departure_keep(usuario.id):
        logger.info(f"{usuario.display_name} (ID: {usuario.id}) voltou para a verificação de saída da guilda, por {interaction.user.display_name} (ID: {interaction.user.id})")
        await interaction.response.send_message(
            f"✅ {usuario.mention} volta a ser verificado. Se não estiver nas guildas do jogo, aparece um card na próxima verificação.",
            ephemeral=True
        )
    else:
        await interaction.response.send_message(f"ℹ️ {usuario.mention} não estava marcado como \"Não saiu\".", ephemeral=True)

if __name__ == "__main__":
    if not DISCORD_TOKEN:
        logger.critical("❌ Erro: DISCORD_TOKEN não encontrado no arquivo .env")
        logger.critical("Por favor, crie um arquivo .env com DISCORD_TOKEN=seu_token_aqui")
    else:
        logger.info("Iniciando bot...")
        try:
            bot.run(DISCORD_TOKEN)
        except Exception as e:
            logger.critical(f"Erro fatal ao iniciar bot: {e}")
            raise
