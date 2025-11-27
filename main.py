import discord
from discord import app_commands
from discord.ext import commands, tasks
import os
import io
import logging
from datetime import datetime
from pytz import timezone
from config import DISCORD_TOKEN, BDO_CLASSES, DATABASE_NAME, DATABASE_URL, ALLOWED_DM_ROLES, NOTIFICATION_CHANNEL_ID, GUILD_MEMBER_ROLE_ID, DM_REPORT_CHANNEL_ID, LIST_CHANNEL_ID, MOVE_LOG_CHANNEL_ID, REGISTERED_ROLE_ID, UNREGISTERED_ROLE_ID, GS_UPDATE_REMINDER_DAYS, GS_REMINDER_CHECK_HOUR
from datetime import timedelta
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
                user_id = str(record[1]) if len(record) > 1 else ''
                family_name = record[2] if len(record) > 2 else 'N/A'
                class_pvp = record[3] if len(record) > 3 else 'N/A'
                ap = record[4] if len(record) > 4 else 0
                aap = record[5] if len(record) > 5 else 0
                dp = record[6] if len(record) > 6 else 0
                updated_at = record[8] if len(record) > 8 else None
            
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
                value="Use o comando `/atualizar` com seus valores atuais de AP, AAP, DP e linkgear.",
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
            embed.add_field(name="🔗 Link Gear", value=linkgear, inline=False)
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
    linkgear="Link do gear (obrigatório)"
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
    linkgear: str
):
    # Deferir resposta IMEDIATAMENTE para evitar timeout
    await interaction.response.defer(ephemeral=True)
    
    try:
        # Validar valores numéricos
        if ap < 0 or aap < 0 or dp < 0:
            await interaction.followup.send(
                "❌ Os valores de AP, AAP e DP devem ser números positivos!",
                ephemeral=True
            )
            return
        
        # Validar linkgear
        if not linkgear or linkgear.strip() == "":
            await interaction.followup.send(
                "❌ O link do gear é obrigatório!",
                ephemeral=True
            )
            return
        
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
        
        # Calcular GS total (MAX(AP, AAP) + DP)
        gs_total = calculate_gs(ap, aap, dp)
        
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
        embed.add_field(name="🔗 Link Gear", value=linkgear, inline=False)
        
        if role_added:
            embed.add_field(name="🎖️ Cargo", value="Cargo da guilda atribuído com sucesso!", inline=False)
        elif role_error:
            embed.add_field(name="⚠️ Aviso", value=f"Não foi possível adicionar o cargo: {role_error}", inline=False)
        
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
@app_commands.default_permissions(administrator=True)
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
    if not interaction.user.guild_permissions.administrator:
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
    linkgear="Link do gear - Obrigatório",
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
    linkgear: str,
    nome_familia: str = None,
    nome_personagem: str = None,
    classe_pvp: str = None
):
    # Deferir resposta IMEDIATAMENTE para evitar timeout
    await interaction.response.defer(ephemeral=True)
    
    try:
        # Validar valores numéricos
        if ap < 0 or aap < 0 or dp < 0:
            await interaction.followup.send(
                "❌ Os valores de AP, AAP e DP devem ser números positivos!",
                ephemeral=True
            )
            return
        
        # Validar linkgear
        if not linkgear or linkgear.strip() == "":
            await interaction.followup.send(
                "❌ O link do gear é obrigatório!",
                ephemeral=True
            )
            return
        
        user_id = str(interaction.user.id)
        
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
                        value=f"`/atualizar ap:300 aap:280 dp:400 linkgear:https://... nome_personagem:NovoNome classe_pvp:{classe_pvp}`",
                        inline=False
                    )
                    await interaction.user.send(embed=dm_embed)
                except:
                    pass
                
                await interaction.followup.send(
                    f"❌ **Nome do personagem obrigatório!**\n\n"
                    f"Você está mudando de classe de **{current_class_pvp}** para **{classe_pvp}**.\n"
                    f"Como você está mudando para um personagem diferente, é **obrigatório** fornecer o nome do novo personagem.\n\n"
                    f"**Exemplo:** `/atualizar ap:{ap} aap:{aap} dp:{dp} linkgear:{linkgear} nome_personagem:NovoNome classe_pvp:{classe_pvp}`",
                    ephemeral=True
                )
                return
        
        # Se não mudou de classe e não forneceu nome_personagem, manter o atual
        if nome_personagem is None:
            nome_personagem = current_character_name
        
        # Atualizar gearscore
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
        
        # Calcular GS total
        gs_total = calculate_gs(ap, aap, dp)
        
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
        embed.add_field(name="🔗 Link Gear", value=linkgear, inline=False)
        
        if current_class_pvp != classe_pvp:
            embed.add_field(
                name="🔄 Mudança de Classe",
                value=f"Classe alterada de **{current_class_pvp}** para **{classe_pvp}**",
                inline=False
            )
        
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
        # SQLite/PostgreSQL: id, user_id, family_name, class_pvp, ap, aap, dp, linkgear, updated_at
        family_name = result[2] if len(result) > 2 else 'N/A'
        character_name = family_name
        class_pvp = result[3] if len(result) > 3 else 'N/A'
        ap = result[4] if len(result) > 4 else 0
        aap = result[5] if len(result) > 5 else 0
        dp = result[6] if len(result) > 6 else 0
        linkgear = result[7] if len(result) > 7 else 'N/A'
        updated_at = result[8] if len(result) > 8 else 'N/A'
    
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
            ap_val = result[4] if len(result) > 4 else 0
            aap_val = result[5] if len(result) > 5 else 0
            dp_val = result[6] if len(result) > 6 else 0
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
        value=f"[Clique aqui]({linkgear})" if linkgear != 'N/A' and linkgear.startswith('http') else linkgear,
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

@bot.tree.command(name="pre", description="[ADMIN] Visualiza o perfil de outro membro")
@app_commands.describe(usuario="Usuário para visualizar o perfil")
@app_commands.default_permissions(administrator=True)
async def pre(interaction: discord.Interaction, usuario: discord.Member):
    """Visualiza o perfil de outro membro (apenas administradores)"""
    if not interaction.user.guild_permissions.administrator:
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
        
        # Buscar membros da classe
        all_gearscores = db.get_all_gearscores(valid_user_ids=self.valid_user_ids)
        
        class_members = []
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
            
            if class_pvp == selected_class:
                gs_total = max(int(ap or 0), int(aap or 0)) + int(dp or 0)
                member = self.guild.get_member(int(user_id)) if user_id else None
                display_name = member.display_name if member else "Desconhecido"
                class_members.append((family_name, display_name, gs_total, ap, aap, dp, user_id, linkgear))
        
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
@app_commands.default_permissions(administrator=True)
async def estatisticas_classes(interaction: discord.Interaction):
    """Mostra estatísticas das classes na guilda (apenas administradores)"""
    if not interaction.user.guild_permissions.administrator:
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
        class_ranking = ""
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
            
            class_ranking += f"{medal} **{class_name}** — {total} membro(s) • GS: {avg_gs_int}\n"
        
        embed.add_field(
            name="🏆 Ranking de Classes (por quantidade)",
            value=class_ranking if class_ranking else "Nenhuma classe encontrada",
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
@app_commands.default_permissions(administrator=True)
async def stats(interaction: discord.Interaction):
    """Mostra lista completa de todos os membros com gearscore (apenas administradores)"""
    if not interaction.user.guild_permissions.administrator:
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
        
        await interaction.response.defer(ephemeral=False)  # Não ephemeral para mostrar para todos
        
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
            
            if page == 0:
                await interaction.followup.send(embed=embed)
            else:
                await interaction.channel.send(embed=embed)
        
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
@app_commands.default_permissions(administrator=True)
async def ranking_gearscore(interaction: discord.Interaction):
    """Mostra o ranking de gearscore (apenas administradores)"""
    if not interaction.user.guild_permissions.administrator:
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
                ap = result[4] if len(result) > 4 else 0
                aap = result[5] if len(result) > 5 else 0
                dp = result[6] if len(result) > 6 else 0
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
                # SQLite/PostgreSQL: id, user_id, family_name, class_pvp, ap, aap, dp, linkgear, updated_at
                family_name = result[2] if len(result) > 2 else 'N/A'
                class_pvp = result[3] if len(result) > 3 else 'N/A'
                ap = result[4] if len(result) > 4 else 0
                aap = result[5] if len(result) > 5 else 0
                dp = result[6] if len(result) > 6 else 0
            
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
@app_commands.default_permissions(administrator=True)
async def membros_classe(interaction: discord.Interaction, classe: str):
    """Visualiza todos os membros registrados de uma classe com todas as informações (apenas administradores)"""
    if not interaction.user.guild_permissions.administrator:
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
                ap = member[4] if len(member) > 4 else 0
                aap = member[5] if len(member) > 5 else 0
                dp = member[6] if len(member) > 6 else 0
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
                # SQLite/PostgreSQL: id, user_id, family_name, class_pvp, ap, aap, dp, linkgear, updated_at
                family_name = member[2] if len(member) > 2 else 'N/A'
                ap = member[4] if len(member) > 4 else 0
                aap = member[5] if len(member) > 5 else 0
                dp = member[6] if len(member) > 6 else 0
                linkgear = member[7] if len(member) > 7 else 'N/A'
                updated_at = member[8] if len(member) > 8 else 'N/A'
            
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
            member_info += f"🔗 **Link Gear:** {linkgear}\n"
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
@app_commands.default_permissions(administrator=True)
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
            family_name = result[2] if len(result) > 2 else 'N/A'
            class_pvp = result[3] if len(result) > 3 else 'N/A'
            ap = result[4] if len(result) > 4 else 0
            aap = result[5] if len(result) > 5 else 0
            dp = result[6] if len(result) > 6 else 0
            linkgear = result[7] if len(result) > 7 else 'N/A'
            updated_at = result[8] if len(result) > 8 else 'N/A'
        
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
        embed.add_field(name="🔗 Link Gear", value=linkgear, inline=False)
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

@bot.tree.command(name="lista", description="Cria uma lista dos membros em um canal de voz")
@app_commands.describe(
    sala="Canal de voz para listar os membros (digite para buscar)",
    nome_lista="Nome da lista"
)
@app_commands.autocomplete(sala=voice_channel_autocomplete)
async def lista(interaction: discord.Interaction, sala: str, nome_lista: str):
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
        members_in_voice = [
            member for member in voice_channel.members
            if not member.bot  # Excluir bots
        ]
        
        if not members_in_voice:
            await interaction.followup.send(
                f"❌ Nenhum membro encontrado no canal de voz **{voice_channel.name}**!",
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
        
        # Criar embed com a lista
        embed = discord.Embed(
            title=f"📋 {nome_lista}",
            description=f"Lista de membros do canal de voz: **{voice_channel.mention}**",
            color=discord.Color.blue(),
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
        
        embed.set_footer(text=f"Lista criada por {interaction.user.display_name}")
        
        # Enviar para o canal de listas
        await list_channel.send(embed=embed)
        
        await interaction.followup.send(
            f"✅ Lista **{nome_lista}** criada com sucesso e enviada para o canal de listas!",
            ephemeral=True
        )
        
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

@bot.tree.command(name="mover_sala", description="[ADMIN] Move todos os membros de uma sala de voz para outra")
@app_commands.describe(
    sala_origem="Canal de voz de origem (digite para buscar)",
    sala_destino="Canal de voz de destino (digite para buscar)"
)
@app_commands.autocomplete(sala_origem=voice_channel_autocomplete)
@app_commands.autocomplete(sala_destino=voice_channel_autocomplete)
@app_commands.default_permissions(administrator=True)
async def mover_sala(interaction: discord.Interaction, sala_origem: str, sala_destino: str):
    """Move todos os membros de uma sala de voz para outra (apenas administradores)"""
    if not interaction.user.guild_permissions.administrator:
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
@app_commands.default_permissions(administrator=True)
async def admin_lista_classe(interaction: discord.Interaction, classe: str):
    """Lista todos os membros de uma classe específica (apenas administradores)"""
    if not interaction.user.guild_permissions.administrator:
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
                # SQLite/PostgreSQL: id, user_id, family_name, class_pvp, ap, aap, dp, linkgear, updated_at
                family = member[2] if len(member) > 2 else 'N/A'
                ap = int(member[4] or 0) if len(member) > 4 else 0
                aap = int(member[5] or 0) if len(member) > 5 else 0
                dp = int(member[6] or 0) if len(member) > 6 else 0
            
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

@bot.tree.command(name="admin_progresso_player", description="[ADMIN] Mostra histórico de progressão de um player")
@app_commands.describe(
    usuario="Usuário do Discord"
)
@app_commands.default_permissions(administrator=True)
async def admin_progresso_player(interaction: discord.Interaction, usuario: discord.Member):
    """Mostra histórico de progressão de um player (apenas administradores)"""
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message(
            "❌ Apenas administradores podem usar este comando!",
            ephemeral=True
        )
        return
    
    try:
        user_id = str(usuario.id)
        
        # Deferir resposta antes de operações que podem demorar
        await interaction.response.defer(ephemeral=True)
        
        # Buscar classe atual do usuário
        current_class = db.get_user_current_class(user_id)
        if not current_class:
            await interaction.followup.send(
                f"❌ {usuario.mention} ainda não possui um registro!",
                ephemeral=True
            )
            return
        
        # Buscar histórico SEM filtro para mostrar todas as classes (incluindo mudanças)
        # Isso permite ver o histórico completo mesmo quando o player mudou de classe
        history = db.get_user_history(user_id, None)
        
        if not history:
            # Verificar se o usuário tem registro atual
            current_gear = db.get_gearscore(user_id)
            if current_gear:
                await interaction.followup.send(
                    f"❌ Nenhum histórico encontrado para {usuario.mention}.\n\n"
                    f"**Informações:**\n"
                    f"• Classe atual: **{current_class}**\n"
                    f"• O histórico é criado automaticamente quando você usa `/registro` ou `/atualizar`\n"
                    f"• Se você acabou de atualizar, o histórico pode ainda não estar disponível\n"
                    f"• Tente atualizar novamente com `/atualizar` para gerar o histórico",
                    ephemeral=True
                )
            else:
                await interaction.followup.send(
                    f"❌ {usuario.mention} ainda não possui um registro!",
                    ephemeral=True
                )
            return
        
        # Calcular progressão
        progress = db.get_user_progress(user_id, current_class)
        
        embed = discord.Embed(
            title=f"📈 Histórico de Progressão - {usuario.display_name}",
            color=discord.Color.green(),
            timestamp=discord.utils.utcnow()
        )
        
        if progress:
            if isinstance(progress, dict):
                first_gs = progress.get('first_gs', 0)
                current_gs = progress.get('current_gs', 0)
                progress_value = progress.get('progress', 0)
                updates = progress.get('updates', 0)
            else:
                first_gs = progress[0] if len(progress) > 0 else 0
                current_gs = progress[1] if len(progress) > 1 else 0
                progress_value = progress[2] if len(progress) > 2 else 0
                updates = progress[3] if len(progress) > 3 else 0
            
            embed.add_field(name="📊 Progressão Total", value=f"**{first_gs}** → **{current_gs}** (+{progress_value})", inline=False)
            embed.add_field(name="🔄 Atualizações", value=f"**{updates}** registro(s)", inline=True)
        
        # Mostrar últimas 10 atualizações
        recent_updates = history[:10]
        updates_text = ""
        for update in recent_updates:
            if isinstance(update, dict):
                update_class = update.get('class_pvp', current_class)
                ap = update.get('ap', 0)
                aap = update.get('aap', 0)
                dp = update.get('dp', 0)
                total = update.get('total_gs', calculate_gs(ap, aap, dp))
                date = update.get('created_at', 'N/A')
            else:
                # Sempre busca sem filtro agora, então sempre retorna 6 campos:
                # class_pvp, ap, aap, dp, total_gs, created_at
                if len(update) >= 6:
                    # Busca sem filtro: class_pvp, ap, aap, dp, total_gs, created_at
                    # Garantir que os valores sejam extraídos corretamente
                    try:
                        # Classe (primeiro campo)
                        update_class = str(update[0]) if update[0] is not None else current_class
                        
                        # Valores numéricos (campos 1, 2, 3, 4)
                        def safe_int(val, default=0):
                            if val is None:
                                return default
                            if isinstance(val, (int, float)):
                                return int(val)
                            if isinstance(val, str):
                                # Remover espaços e tentar converter
                                val_clean = val.strip()
                                if val_clean.isdigit():
                                    return int(val_clean)
                            return default
                        
                        ap = safe_int(update[1])
                        aap = safe_int(update[2])
                        dp = safe_int(update[3])
                        total = safe_int(update[4], calculate_gs(ap, aap, dp))
                        date = update[5] if len(update) > 5 else 'N/A'
                    except (ValueError, TypeError, IndexError) as e:
                        # Se houver erro, tentar valores padrão e logar
                        print(f"⚠️ Erro ao processar histórico: {e}, update: {update}")
                        update_class = current_class
                        ap = 0
                        aap = 0
                        dp = 0
                        total = 0
                        date = 'N/A'
                elif len(update) == 5:
                    # Formato antigo (caso ainda exista): ap, aap, dp, total_gs, created_at
                    update_class = current_class
                    ap = int(update[0]) if len(update) > 0 and update[0] is not None else 0
                    aap = int(update[1]) if len(update) > 1 and update[1] is not None else 0
                    dp = int(update[2]) if len(update) > 2 and update[2] is not None else 0
                    total = int(update[3]) if len(update) > 3 and update[3] is not None else calculate_gs(ap, aap, dp)
                    date = update[4] if len(update) > 4 else 'N/A'
                else:
                    # Formato desconhecido, tentar valores padrão
                    update_class = current_class
                    ap = 0
                    aap = 0
                    dp = 0
                    total = 0
                    date = 'N/A'
            
            # Formatar data e horário corretamente
            if date == 'N/A' or date is None:
                date_str = 'N/A'
            elif hasattr(date, 'strftime'):
                # Objeto datetime do PostgreSQL (datetime.datetime ou datetime.date)
                try:
                    date_str = date.strftime("%d/%m/%Y às %H:%M")
                except:
                    # Se não tiver hora, só data
                    try:
                        date_str = date.strftime("%d/%m/%Y")
                    except:
                        date_str = str(date)
            elif isinstance(date, str):
                # Tentar parsear se for string ISO ou timestamp
                try:
                    from datetime import datetime
                    # Tentar diferentes formatos
                    if 'T' in date:
                        # Formato ISO: 2024-11-23T22:52:00 ou 2024-11-23T22:52:00.000000
                        date_clean = date.replace('Z', '+00:00').split('+')[0].split('.')[0]
                        dt = datetime.fromisoformat(date_clean)
                        date_str = dt.strftime("%d/%m/%Y às %H:%M")
                    elif date.replace('.', '').isdigit():
                        # Timestamp Unix (pode ter decimais)
                        dt = datetime.fromtimestamp(float(date))
                        date_str = dt.strftime("%d/%m/%Y às %H:%M")
                    else:
                        date_str = date
                except Exception as e:
                    # Se falhar, usar a string original
                    date_str = date
            else:
                # Tentar converter para string
                date_str = str(date)
            
            updates_text += f"**{update_class}**: {total} GS ({ap}/{aap}/{dp}) - {date_str}\n"
        
        if updates_text:
            embed.add_field(name="📝 Últimas Atualizações", value=updates_text[:1024], inline=False)
        
        embed.set_footer(text=f"Histórico de {usuario.display_name}")
        await interaction.followup.send(embed=embed, ephemeral=True)
    
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        logger.error(f"Erro ao buscar histórico: {error_details}")
        
        # Verificar se já respondeu
        if interaction.response.is_done():
            await interaction.followup.send(
                f"❌ Erro ao buscar histórico: {str(e)}\n\n"
                f"**Detalhes técnicos:** Verifique os logs do bot para mais informações.",
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"❌ Erro ao buscar histórico: {str(e)}\n\n"
                f"**Detalhes técnicos:** Verifique os logs do bot para mais informações.",
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
# @app_commands.default_permissions(administrator=True)
# async def admin_limpar_banco(interaction: discord.Interaction, tipo: app_commands.Choice[str], confirmar: str):
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
        
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        logger.error(f"Erro ao buscar histórico: {error_details}")
        
        # Verificar se já respondeu
        if interaction.response.is_done():
            await interaction.followup.send(
                f"❌ Erro ao buscar histórico: {str(e)}\n\n"
                f"**Detalhes técnicos:** Verifique os logs do bot para mais informações.",
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"❌ Erro ao buscar histórico: {str(e)}\n\n"
                f"**Detalhes técnicos:** Verifique os logs do bot para mais informações.",
                ephemeral=True
            )

@bot.tree.command(name="analise_classe", description="[ADMIN] Análise completa de uma classe com relatório detalhado de todos os membros")
@app_commands.describe(
    classe="Classe a ser analisada (digite para buscar)"
)
@app_commands.autocomplete(classe=classe_autocomplete)
@app_commands.default_permissions(administrator=True)
async def analise_classe(interaction: discord.Interaction, classe: str):
    """Análise completa de uma classe com relatório detalhado de todos os membros (apenas administradores)"""
    if not interaction.user.guild_permissions.administrator:
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
                # SQLite/PostgreSQL: id, user_id, family_name, class_pvp, ap, aap, dp, linkgear, updated_at
                ap = int(member[4] or 0) if len(member) > 4 else 0
                aap = int(member[5] or 0) if len(member) > 5 else 0
                dp = int(member[6] or 0) if len(member) > 6 else 0
            
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
                # SQLite/PostgreSQL: id, user_id, family_name, class_pvp, ap, aap, dp, linkgear, updated_at
                family_name = member[2] if len(member) > 2 else 'N/A'
                ap = int(member[4] or 0) if len(member) > 4 else 0
                aap = int(member[5] or 0) if len(member) > 5 else 0
                dp = int(member[6] or 0) if len(member) > 6 else 0
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
                ap = int(member[4] or 0) if len(member) > 4 else 0
                aap = int(member[5] or 0) if len(member) > 5 else 0
                dp = int(member[6] or 0) if len(member) > 6 else 0
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
                    # SQLite/PostgreSQL: id, user_id, family_name, class_pvp, ap, aap, dp, linkgear, updated_at
                    family_name = member[2] if len(member) > 2 else 'N/A'
                    ap = int(member[4] or 0) if len(member) > 4 else 0
                    aap = int(member[5] or 0) if len(member) > 5 else 0
                    dp = int(member[6] or 0) if len(member) > 6 else 0
                    linkgear = member[7] if len(member) > 7 else 'N/A'
                
                gs_total = calculate_gs(ap, aap, dp)
                position = embed_idx + i
                
                # Criar texto do membro
                member_info = f"**GS:** {gs_total}\n"
                member_info += f"⚔️ AP: {ap} | 🔥 AAP: {aap} | 🛡️ DP: {dp}\n"
                member_info += f"🔗 **Link Gear:** {linkgear}"
                
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
@app_commands.default_permissions(administrator=True)
async def admin_membros_sem_registro(interaction: discord.Interaction):
    """Lista membros com cargo da guilda que ainda não fizeram registro (apenas administradores)"""
    if not interaction.user.guild_permissions.administrator:
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
                # SQLite/PostgreSQL: id, user_id, family_name, class_pvp, ap, aap, dp, linkgear, updated_at
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
@app_commands.default_permissions(administrator=True)
async def admin_enviar_lembretes(interaction: discord.Interaction):
    """Envia lembretes de atualização de GS manualmente (apenas administradores)"""
    if not interaction.user.guild_permissions.administrator:
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
@app_commands.default_permissions(administrator=True)
async def admin_gs_desatualizados(interaction: discord.Interaction, dias: int = None):
    """Lista membros que não atualizaram GS há X dias (apenas administradores)"""
    if not interaction.user.guild_permissions.administrator:
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
                    user_id = str(record[1]) if len(record) > 1 else ''
                    family_name = record[2] if len(record) > 2 else 'N/A'
                    class_pvp = record[3] if len(record) > 3 else 'N/A'
                    ap = record[4] if len(record) > 4 else 0
                    aap = record[5] if len(record) > 5 else 0
                    dp = record[6] if len(record) > 6 else 0
                    updated_at = record[8] if len(record) > 8 else None
                
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

