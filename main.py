import discord
from discord import app_commands
from discord.ext import commands
import os
import io
from datetime import datetime
from pytz import timezone
from config import DISCORD_TOKEN, BDO_CLASSES, DATABASE_NAME, DATABASE_URL, ALLOWED_DM_ROLES, NOTIFICATION_CHANNEL_ID, GUILD_MEMBER_ROLE_ID, DM_REPORT_CHANNEL_ID, LIST_CHANNEL_ID, MOVE_LOG_CHANNEL_ID
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

# Inicializar banco de dados
db = Database()

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
    except Exception as e:
        # Não interromper o fluxo principal se houver erro ao enviar notificação
        print(f"Erro ao enviar notificação ao canal: {str(e)}")

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
    except Exception as e:
        # Não interromper o fluxo principal se houver erro ao enviar log
        print(f"Erro ao enviar log de movimentação ao canal: {str(e)}")

@bot.event
async def on_ready():
    print(f'{bot.user} está online!')
    try:
        synced = await bot.tree.sync()
        print(f'Sincronizados {len(synced)} comando(s)')
    except Exception as e:
        print(f'Erro ao sincronizar comandos: {e}')

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
                      "`/ver_gearscore` - Visualiza seu gearscore\n"
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

# Autocomplete para classe PVP (limite de 25 resultados)
async def classe_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    """Autocomplete para classes do BDO"""
    # Filtrar classes que começam com o texto digitado (case-insensitive)
    filtered = [
        classe for classe in BDO_CLASSES 
        if current.lower() in classe.lower()
    ][:25]  # Limitar a 25 resultados
    return [app_commands.Choice(name=classe, value=classe) for classe in filtered]

@bot.tree.command(name="registro", description="Registra seu gearscore pela primeira vez")
@app_commands.describe(
    nome_familia="Nome da família do personagem",
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
    classe_pvp: str,
    ap: int,
    aap: int,
    dp: int,
    linkgear: str
):
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
        user_id = str(interaction.user.id)
        
        # Verificar se é em um servidor (não DM)
        if not interaction.guild:
            await interaction.response.send_message(
                "❌ Este comando só pode ser usado em um servidor!",
                ephemeral=True
            )
            return
        
        # Deferir resposta se a operação pode demorar (privado)
        await interaction.response.defer(ephemeral=True)
        
        # Registrar gearscore
        db.register_gearscore(
            user_id=user_id,
            family_name=nome_familia,
            class_pvp=classe_pvp,
            ap=ap,
            aap=aap,
            dp=dp,
            linkgear=linkgear
        )
        
        # Adicionar cargo da guilda ao membro
        member = interaction.guild.get_member(interaction.user.id)
        role_added = False
        role_error = None
        
        if member:
            role = interaction.guild.get_role(GUILD_MEMBER_ROLE_ID)
            if role:
                try:
                    if not has_guild_role(member):
                        await member.add_roles(role, reason="Registro de gearscore - membro da guilda")
                        role_added = True
                except discord.Forbidden:
                    role_error = "Sem permissão para adicionar cargo"
                except discord.HTTPException as e:
                    role_error = f"Erro ao adicionar cargo: {str(e)}"
            else:
                role_error = "Cargo da guilda não encontrado no servidor"
        else:
            role_error = "Membro não encontrado no servidor"
        
        # Calcular GS total (MAX(AP, AAP) + DP)
        gs_total = calculate_gs(ap, aap, dp)
        
        embed = discord.Embed(
            title="✅ Gearscore Registrado!",
            color=discord.Color.green(),
            timestamp=discord.utils.utcnow()
        )
        embed.add_field(name="👤 Família", value=nome_familia, inline=True)
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
    nome_familia="Nome da família do personagem",
    classe_pvp="Classe PVP do personagem (digite para buscar)",
    ap="Attack Power (AP)",
    aap="Awakened Attack Power (AAP)",
    dp="Defense Power (DP)",
    linkgear="Link do gear (obrigatório)"
)
@app_commands.autocomplete(classe_pvp=classe_autocomplete)
async def atualizar(
    interaction: discord.Interaction,
    nome_familia: str,
    classe_pvp: str,
    ap: int,
    aap: int,
    dp: int,
    linkgear: str
):
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
        user_id = str(interaction.user.id)
        
        # Verificar se já existe registro (operação rápida)
        current_class = db.get_user_current_class(user_id)
        if not current_class:
            await interaction.response.send_message(
                "❌ Você ainda não possui um registro! Use `/registro` primeiro.",
                ephemeral=True
            )
            return
        
        # Deferir resposta antes de operações que podem demorar (privado)
        await interaction.response.defer(ephemeral=True)
        
        # Atualizar gearscore (pode demorar com banco de dados)
        db.update_gearscore(
            user_id=user_id,
            family_name=nome_familia,
            class_pvp=classe_pvp,
            ap=ap,
            aap=aap,
            dp=dp,
            linkgear=linkgear
        )
        
        # Calcular GS total (MAX(AP, AAP) + DP)
        gs_total = calculate_gs(ap, aap, dp)
        
        embed = discord.Embed(
            title="✅ Gearscore Atualizado!",
            color=discord.Color.green(),
            timestamp=discord.utils.utcnow()
        )
        embed.add_field(name="👤 Família", value=nome_familia, inline=True)
        embed.add_field(name="🎭 Classe PVP", value=classe_pvp, inline=True)
        embed.add_field(name="⚔️ AP", value=f"{ap}", inline=True)
        embed.add_field(name="🔥 AAP", value=f"{aap}", inline=True)
        embed.add_field(name="🛡️ DP", value=f"{dp}", inline=True)
        embed.add_field(name="📊 GS Total", value=f"**{gs_total}** (MAX({ap}, {aap}) + {dp})", inline=False)
        embed.add_field(name="🔗 Link Gear", value=linkgear, inline=False)
        
        if current_class != classe_pvp:
            embed.add_field(
                name="🔄 Mudança de Classe",
                value=f"Classe alterada de **{current_class}** para **{classe_pvp}**",
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
        # Verificar se já respondeu (defer foi chamado)
        if interaction.response.is_done():
            await interaction.followup.send(
                f"❌ Erro ao atualizar gearscore: {str(e)}",
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"❌ Erro ao atualizar gearscore: {str(e)}",
                ephemeral=True
            )

@bot.tree.command(name="ver_gearscore", description="Visualiza o seu gearscore")
async def ver_gearscore(interaction: discord.Interaction):
    try:
        user_id = str(interaction.user.id)
        results = db.get_gearscore(user_id)
        
        if not results:
            await interaction.response.send_message(
                "❌ Nenhum gearscore encontrado! Use `/registro` para registrar seu gearscore.",
                ephemeral=True
            )
            return
        
        # Agora só pode ter 1 resultado (1 classe por usuário)
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
            # SQLite/PostgreSQL: id, user_id, family_name, class_pvp, ap, aap, dp, linkgear, updated_at
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
        
        await interaction.response.send_message(embed=embed, ephemeral=True)
            
    except Exception as e:
        await interaction.response.send_message(
            f"❌ Erro ao buscar gearscore: {str(e)}",
            ephemeral=True
        )

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
    try:
        # Buscar apenas membros que têm o cargo da guilda
        valid_user_ids = await get_guild_member_ids(interaction.guild)
        
        if not valid_user_ids:
            await interaction.response.send_message(
                "❌ Nenhum membro com o cargo da guilda encontrado!",
                ephemeral=True
            )
            return
        
        stats = db.get_class_statistics(valid_user_ids=valid_user_ids)
        
        if not stats:
            await interaction.response.send_message(
                "❌ Nenhum gearscore cadastrado ainda!",
                ephemeral=True
            )
            return
        
        # Calcular GS médio geral
        total_chars = 0
        total_weighted_gs = 0
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
            stats_list.append((class_name, total, avg_gs))
        
        # Calcular GS médio geral (média ponderada)
        overall_avg_gs = int(round(total_weighted_gs / total_chars)) if total_chars > 0 else 0
        
        embed = discord.Embed(
            title="🎭 Estatísticas das Classes - Guilda",
            description="Distribuição e GS médio por classe",
            color=discord.Color.purple(),
            timestamp=discord.utils.utcnow()
        )
        
        # Adicionar GS médio geral no topo
        embed.add_field(
            name="📊 GS Médio Geral",
            value=f"**{overall_avg_gs}** (todas as classes)",
            inline=False
        )
        
        # Adicionar campos das classes individuais
        for class_name, total, avg_gs in stats_list:
            avg_gs_int = int(round(avg_gs)) if avg_gs else 0
            embed.add_field(
                name=f"{class_name}",
                value=f"👥 **{total}** membro(s)\n📊 GS Médio: **{avg_gs_int}**",
                inline=True
            )
        
        embed.set_footer(text=f"Total de {total_chars} personagens cadastrados (apenas membros com cargo da guilda)")
        await interaction.response.send_message(embed=embed)
        
    except Exception as e:
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
        print(f"Erro ao buscar membros da classe: {error_details}")
        
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
            title="📨 Mensagem do Bot",
            description=mensagem,
            color=discord.Color.blue(),
            timestamp=discord.utils.utcnow()
        )
        embed.set_footer(text=f"Enviado por {interaction.user.display_name}")
        
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
        print(f"Erro ao criar lista: {error_details}")
        
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
                print(f"Erro ao mover {member.display_name}: {str(e)}")
        
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
        print(f"Erro ao mover membros: {error_details}")
        
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
        
        # Footer nas DMs sempre mostra "Staff MOUZ"
        embed.set_footer(text="Staff MOUZ")
        
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
                print(f"Erro ao enviar DM para {member.display_name}: {str(e)}")
        
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
            print(f"Erro ao enviar relatório no canal: {str(e)}")
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
                ap = member.get('ap', 0)
                aap = member.get('aap', 0)
                dp = member.get('dp', 0)
            else:
                # SQLite/PostgreSQL: id, user_id, family_name, class_pvp, ap, aap, dp, linkgear, updated_at
                family = member[2] if len(member) > 2 else 'N/A'
                ap = member[4] if len(member) > 4 else 0
                aap = member[5] if len(member) > 5 else 0
                dp = member[6] if len(member) > 6 else 0
            
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
        print(f"Erro ao buscar histórico: {error_details}")
        
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
        print(f"Erro ao buscar histórico: {error_details}")
        
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

@bot.tree.command(name="admin_gs_medio_classe", description="[ADMIN] Mostra GS médio detalhado de uma classe")
@app_commands.describe(
    classe="Classe a ser analisada (digite para buscar)"
)
@app_commands.autocomplete(classe=classe_autocomplete)
@app_commands.default_permissions(administrator=True)
async def admin_gs_medio_classe(interaction: discord.Interaction, classe: str):
    """Mostra GS médio detalhado de uma classe (apenas administradores)"""
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
                ap = member.get('ap', 0)
                aap = member.get('aap', 0)
                dp = member.get('dp', 0)
            else:
                ap = member[5] if len(member) > 5 else 0
                aap = member[6] if len(member) > 6 else 0
                dp = member[7] if len(member) > 7 else 0
            
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
                ap = member.get('ap', 0)
                aap = member.get('aap', 0)
                dp = member.get('dp', 0)
                gs = calculate_gs(ap, aap, dp)
            else:
                # SQLite/PostgreSQL: id, user_id, family_name, class_pvp, ap, aap, dp, linkgear, updated_at
                family_name = member[2] if len(member) > 2 else 'N/A'
                ap = member[4] if len(member) > 4 else 0
                aap = member[5] if len(member) > 5 else 0
                dp = member[6] if len(member) > 6 else 0
                gs = calculate_gs(ap, aap, dp)
            
            medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"#{i}"
            top_text += f"{medal} **{family_name}** - {gs} GS\n"
        
        if top_text:
            embed.add_field(name="🏆 Top 5 da Classe", value=top_text, inline=False)
        
        await interaction.followup.send(embed=embed, ephemeral=True)
        
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
        print(f"Erro ao buscar membros sem registro: {error_details}")
        
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

if __name__ == "__main__":
    if not DISCORD_TOKEN:
        print("❌ Erro: DISCORD_TOKEN não encontrado no arquivo .env")
        print("Por favor, crie um arquivo .env com DISCORD_TOKEN=seu_token_aqui")
    else:
        bot.run(DISCORD_TOKEN)

