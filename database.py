import sqlite3
import os
from config import DATABASE_NAME

class Database:
    def __init__(self):
        self.db_path = DATABASE_NAME
        self.init_database()
    
    def get_connection(self):
        """Retorna uma conexão com o banco de dados"""
        return sqlite3.connect(self.db_path)
    
    def init_database(self):
        """Inicializa o banco de dados criando a tabela se não existir"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS gearscore (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                family_name TEXT NOT NULL,
                character_name TEXT,
                class_pvp TEXT NOT NULL,
                ap INTEGER NOT NULL,
                aap INTEGER NOT NULL,
                dp INTEGER NOT NULL,
                linkgear TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, class_pvp)
            )
        ''')
        
        # Adicionar coluna character_name se não existir (migração)
        try:
            cursor.execute('ALTER TABLE gearscore ADD COLUMN character_name TEXT')
        except sqlite3.OperationalError:
            pass  # Coluna já existe
        
        # Tabela de histórico para rastrear progressão
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS gearscore_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                class_pvp TEXT NOT NULL,
                ap INTEGER NOT NULL,
                aap INTEGER NOT NULL,
                dp INTEGER NOT NULL,
                total_gs INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Índice para melhor performance em consultas de histórico
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_history_user_class 
            ON gearscore_history(user_id, class_pvp, created_at)
        ''')
        
        # Tabela de eventos (GvG, Treino, etc)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS eventos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tipo TEXT NOT NULL,
                nome TEXT NOT NULL,
                canal_voz TEXT,
                criado_por TEXT NOT NULL,
                criado_por_nome TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                mes_referencia TEXT NOT NULL
            )
        ''')
        
        # Tabela de participações em eventos
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS participacoes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                evento_id INTEGER NOT NULL,
                user_id TEXT NOT NULL,
                family_name TEXT,
                display_name TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (evento_id) REFERENCES eventos(id)
            )
        ''')
        
        # Índices para performance
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_eventos_mes 
            ON eventos(mes_referencia, tipo)
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_participacoes_evento 
            ON participacoes(evento_id, user_id)
        ''')
        
        conn.commit()
        conn.close()
    
    def register_gearscore(self, user_id, family_name, class_pvp, ap, aap, dp, linkgear, character_name=None):
        """Registra um novo gearscore (primeira vez)"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        # Verificar se já existe registro para esta classe
        cursor.execute('''
            SELECT class_pvp FROM gearscore 
            WHERE user_id = ? AND class_pvp = ?
        ''', (user_id, class_pvp))
        existing = cursor.fetchone()
        
        if existing:
            conn.close()
            raise ValueError(f"Você já possui um registro para a classe {class_pvp}. Use /atualizar para modificar.")
        
        # Inserir novo registro
        cursor.execute('''
            INSERT INTO gearscore 
            (user_id, family_name, character_name, class_pvp, ap, aap, dp, linkgear, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ''', (user_id, family_name, character_name, class_pvp, ap, aap, dp, linkgear))
        
        # Salvar histórico
        total_gs = max(ap, aap) + dp
        cursor.execute('''
            INSERT INTO gearscore_history 
            (user_id, class_pvp, ap, aap, dp, total_gs, created_at)
            VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ''', (user_id, class_pvp, ap, aap, dp, total_gs))
        
        conn.commit()
        conn.close()
    
    def get_user_current_data(self, user_id):
        """Retorna os dados atuais do usuário (family_name, character_name, class_pvp)"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT family_name, character_name, class_pvp FROM gearscore 
            WHERE user_id = ?
            LIMIT 1
        ''', (user_id,))
        result = cursor.fetchone()
        conn.close()
        return result
    
    def update_gearscore(self, user_id, family_name=None, class_pvp=None, ap=None, aap=None, dp=None, linkgear=None, character_name=None):
        """Atualiza o gearscore de um personagem (pode mudar de classe)"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        # Buscar dados atuais
        current_data = self.get_user_current_data(user_id)
        if not current_data:
            conn.close()
            raise ValueError("Você ainda não possui um registro! Use /registro primeiro.")
        
        current_family_name, current_character_name, current_class_pvp = current_data
        
        # Se não forneceu classe_pvp, usar a atual
        if class_pvp is None:
            class_pvp = current_class_pvp
        
        # Usar valores atuais se não fornecidos
        if family_name is None:
            family_name = current_family_name
        
        # character_name pode ser None se mudou de classe (personagem diferente)
        # Se não foi fornecido e não mudou de classe, manter o atual
        # Se mudou de classe e não forneceu, manter None (será limpo)
        if character_name is None:
            if class_pvp == current_class_pvp:
                # Não mudou de classe, manter o nome atual
                character_name = current_character_name
            # Se mudou de classe, character_name permanece None (será limpo)
        if ap is None or aap is None or dp is None or linkgear is None:
            # Buscar valores atuais de AP, AAP, DP e linkgear
            cursor.execute('''
                SELECT ap, aap, dp, linkgear FROM gearscore 
                WHERE user_id = ?
            ''', (user_id,))
            current_values = cursor.fetchone()
            if current_values:
                if ap is None:
                    ap = current_values[0]
                if aap is None:
                    aap = current_values[1]
                if dp is None:
                    dp = current_values[2]
                if linkgear is None:
                    linkgear = current_values[3]
        
        # Verificar se mudou de classe
        if class_pvp != current_class_pvp:
            # Remover registro da classe antiga
            cursor.execute('''
                DELETE FROM gearscore 
                WHERE user_id = ? AND class_pvp = ?
            ''', (user_id, current_class_pvp))
        
        # Atualizar ou inserir gearscore
        cursor.execute('''
            INSERT OR REPLACE INTO gearscore 
            (user_id, family_name, character_name, class_pvp, ap, aap, dp, linkgear, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ''', (user_id, family_name, character_name, class_pvp, ap, aap, dp, linkgear))
        
        # Salvar histórico
        total_gs = max(ap, aap) + dp
        cursor.execute('''
            INSERT INTO gearscore_history 
            (user_id, class_pvp, ap, aap, dp, total_gs, created_at)
            VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ''', (user_id, class_pvp, ap, aap, dp, total_gs))
        
        conn.commit()
        conn.close()
    
    def get_gearscore(self, user_id, class_pvp=None):
        """Busca o gearscore de um usuário"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        # Usar colunas explícitas para garantir ordem consistente
        if class_pvp:
            cursor.execute('''
                SELECT id, user_id, family_name, character_name, class_pvp, ap, aap, dp, linkgear, updated_at
                FROM gearscore 
                WHERE user_id = ? AND class_pvp = ?
            ''', (user_id, class_pvp))
        else:
            cursor.execute('''
                SELECT id, user_id, family_name, character_name, class_pvp, ap, aap, dp, linkgear, updated_at
                FROM gearscore 
                WHERE user_id = ?
                ORDER BY updated_at DESC
            ''', (user_id,))
        
        result = cursor.fetchall()
        conn.close()
        return result
    
    def get_user_current_class(self, user_id):
        """Retorna a classe atual do usuário"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT class_pvp FROM gearscore 
            WHERE user_id = ?
            LIMIT 1
        ''', (user_id,))
        
        result = cursor.fetchone()
        conn.close()
        return result[0] if result else None
    
    def get_all_gearscores(self, valid_user_ids=None):
        """
        Busca todos os gearscores
        
        Args:
            valid_user_ids: Set ou lista de user_ids válidos (que têm o cargo da guilda).
                           Se None, retorna todos os registros.
        """
        conn = self.get_connection()
        cursor = conn.cursor()
        
        # Usar colunas explícitas para garantir ordem consistente
        # (importante porque ALTER TABLE ADD COLUMN adiciona no final)
        if valid_user_ids:
            placeholders = ','.join(['?'] * len(valid_user_ids))
            query = f'''
                SELECT id, user_id, family_name, character_name, class_pvp, ap, aap, dp, linkgear, updated_at
                FROM gearscore 
                WHERE user_id IN ({placeholders})
                ORDER BY updated_at DESC
            '''
            cursor.execute(query, list(valid_user_ids))
        else:
            cursor.execute('''
                SELECT id, user_id, family_name, character_name, class_pvp, ap, aap, dp, linkgear, updated_at
                FROM gearscore 
                ORDER BY updated_at DESC
            ''')
        
        result = cursor.fetchall()
        conn.close()
        return result
    
    def get_class_statistics(self, valid_user_ids=None):
        """
        Retorna estatísticas por classe
        
        Args:
            valid_user_ids: Set ou lista de user_ids válidos (que têm o cargo da guilda).
                           Se None, retorna todos os registros.
        """
        conn = self.get_connection()
        cursor = conn.cursor()
        
        if valid_user_ids:
            # Criar placeholders para a query IN
            placeholders = ','.join(['?'] * len(valid_user_ids))
            query = f'''
                SELECT 
                    class_pvp,
                    COUNT(*) as total,
                    AVG(CASE WHEN ap > aap THEN ap ELSE aap END + dp) as avg_gs,
                    AVG(ap) as avg_ap,
                    AVG(aap) as avg_aap,
                    AVG(dp) as avg_dp
                FROM gearscore
                WHERE user_id IN ({placeholders})
                GROUP BY class_pvp
                ORDER BY total DESC, avg_gs DESC
            '''
            cursor.execute(query, list(valid_user_ids))
        else:
            cursor.execute('''
                SELECT 
                    class_pvp,
                    COUNT(*) as total,
                    AVG(CASE WHEN ap > aap THEN ap ELSE aap END + dp) as avg_gs,
                    AVG(ap) as avg_ap,
                    AVG(aap) as avg_aap,
                    AVG(dp) as avg_dp
                FROM gearscore
                GROUP BY class_pvp
                ORDER BY total DESC, avg_gs DESC
            ''')
        
        result = cursor.fetchall()
        conn.close()
        return result
    
    def get_class_members(self, class_pvp, valid_user_ids=None):
        """
        Retorna todos os membros de uma classe específica
        
        Args:
            class_pvp: Nome da classe
            valid_user_ids: Set ou lista de user_ids válidos (que têm o cargo da guilda).
                           Se None, retorna todos os registros.
        """
        conn = self.get_connection()
        cursor = conn.cursor()
        
        # Usar colunas explícitas para garantir ordem consistente
        if valid_user_ids:
            placeholders = ','.join(['?'] * len(valid_user_ids))
            query = f'''
                SELECT id, user_id, family_name, character_name, class_pvp, ap, aap, dp, linkgear, updated_at
                FROM gearscore 
                WHERE class_pvp = ? AND user_id IN ({placeholders})
                ORDER BY (CASE WHEN ap > aap THEN ap ELSE aap END + dp) DESC
            '''
            cursor.execute(query, [class_pvp] + list(valid_user_ids))
        else:
            cursor.execute('''
                SELECT id, user_id, family_name, character_name, class_pvp, ap, aap, dp, linkgear, updated_at
                FROM gearscore 
                WHERE class_pvp = ?
                ORDER BY (CASE WHEN ap > aap THEN ap ELSE aap END + dp) DESC
            ''', (class_pvp,))
        
        result = cursor.fetchall()
        conn.close()
        return result
    
    def get_user_history(self, user_id, class_pvp=None):
        """Retorna histórico de progressão de um usuário"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        if class_pvp:
            cursor.execute('''
                SELECT ap, aap, dp, total_gs, created_at
                FROM gearscore_history
                WHERE user_id = ? AND class_pvp = ?
                ORDER BY created_at DESC
                LIMIT 50
            ''', (user_id, class_pvp))
        else:
            cursor.execute('''
                SELECT class_pvp, ap, aap, dp, total_gs, created_at
                FROM gearscore_history
                WHERE user_id = ?
                ORDER BY created_at DESC
                LIMIT 50
            ''', (user_id,))
        
        result = cursor.fetchall()
        conn.close()
        return result
    
    def get_user_progress(self, user_id, class_pvp):
        """Calcula progressão de um personagem"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT 
                MIN(total_gs) as first_gs,
                MAX(total_gs) as current_gs,
                MAX(total_gs) - MIN(total_gs) as progress,
                COUNT(*) as updates,
                MIN(created_at) as first_update,
                MAX(created_at) as last_update
            FROM gearscore_history
            WHERE user_id = ? AND class_pvp = ?
        ''', (user_id, class_pvp))
        
        result = cursor.fetchone()
        conn.close()
        return result
    
    def clear_all_data(self):
        """Limpa todos os dados do banco (gearscore e histórico)"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        try:
            cursor.execute('DELETE FROM gearscore_history')
            cursor.execute('DELETE FROM gearscore')
            conn.commit()
            conn.close()
            return True, "Todos os dados foram limpos com sucesso!"
        except Exception as e:
            conn.rollback()
            conn.close()
            return False, f"Erro ao limpar banco de dados: {str(e)}"
    
    def clear_history_only(self):
        """Limpa apenas o histórico, mantendo os gearscores atuais"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        try:
            cursor.execute('DELETE FROM gearscore_history')
            deleted_count = cursor.rowcount
            conn.commit()
            conn.close()
            return True, f"Histórico limpo! {deleted_count} registro(s) removido(s)."
        except Exception as e:
            conn.rollback()
            conn.close()
            return False, f"Erro ao limpar histórico: {str(e)}"
    
    def delete_user_gearscore(self, user_id):
        """Deleta o registro de gearscore de um usuário específico"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        try:
            # Verificar se o usuário tem registro
            cursor.execute('SELECT family_name, class_pvp FROM gearscore WHERE user_id = ?', (user_id,))
            result = cursor.fetchone()
            
            if not result:
                conn.close()
                return False, "Usuário não possui registro de gearscore."
            
            family_name, class_pvp = result
            
            # Deletar registro do gearscore
            cursor.execute('DELETE FROM gearscore WHERE user_id = ?', (user_id,))
            
            # Deletar histórico do usuário
            cursor.execute('DELETE FROM gearscore_history WHERE user_id = ?', (user_id,))
            
            conn.commit()
            conn.close()
            return True, f"Registro de {family_name} ({class_pvp}) excluído com sucesso!"
        except Exception as e:
            conn.rollback()
            conn.close()
            return False, f"Erro ao excluir registro: {str(e)}"
    
    def admin_update_gearscore(self, user_id, family_name=None, character_name=None, class_pvp=None, ap=None, aap=None, dp=None, linkgear=None):
        """Atualiza o gearscore de um usuário (admin - força atualização mesmo se não existir)"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        try:
            # Buscar dados atuais
            cursor.execute('''
                SELECT family_name, character_name, class_pvp, ap, aap, dp, linkgear 
                FROM gearscore WHERE user_id = ?
            ''', (user_id,))
            current = cursor.fetchone()
            
            if not current:
                conn.close()
                return False, "Usuário não possui registro de gearscore. Use /registro_manual primeiro."
            
            # Usar valores atuais se não fornecidos
            current_family_name, current_character_name, current_class_pvp, current_ap, current_aap, current_dp, current_linkgear = current
            
            family_name = family_name if family_name is not None else current_family_name
            character_name = character_name if character_name is not None else current_character_name
            class_pvp = class_pvp if class_pvp is not None else current_class_pvp
            ap = ap if ap is not None else current_ap
            aap = aap if aap is not None else current_aap
            dp = dp if dp is not None else current_dp
            linkgear = linkgear if linkgear is not None else current_linkgear
            
            # Se mudou de classe, precisamos atualizar o registro corretamente
            if class_pvp != current_class_pvp:
                # Remover registro da classe antiga
                cursor.execute('DELETE FROM gearscore WHERE user_id = ?', (user_id,))
            
            # Atualizar ou inserir gearscore
            cursor.execute('''
                INSERT OR REPLACE INTO gearscore 
                (user_id, family_name, character_name, class_pvp, ap, aap, dp, linkgear, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ''', (user_id, family_name, character_name, class_pvp, ap, aap, dp, linkgear))
            
            # Salvar histórico
            total_gs = max(ap, aap) + dp
            cursor.execute('''
                INSERT INTO gearscore_history 
                (user_id, class_pvp, ap, aap, dp, total_gs, created_at)
                VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ''', (user_id, class_pvp, ap, aap, dp, total_gs))
            
            conn.commit()
            conn.close()
            return True, f"Registro de {family_name} atualizado com sucesso! GS: {total_gs}"
        except Exception as e:
            conn.rollback()
            conn.close()
            return False, f"Erro ao atualizar registro: {str(e)}"
    
    def get_gearscore_by_family_name(self, family_name):
        """Busca o gearscore de um usuário pelo nome de família (case-insensitive)"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT id, user_id, family_name, character_name, class_pvp, ap, aap, dp, linkgear, updated_at
            FROM gearscore 
            WHERE LOWER(family_name) = LOWER(?)
        ''', (family_name,))
        
        result = cursor.fetchone()
        conn.close()
        return result
    
    def get_gearscores_by_family_names(self, family_names):
        """Busca o gearscore de múltiplos usuários pelos nomes de família"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        results = {}
        for name in family_names:
            cursor.execute('''
                SELECT id, user_id, family_name, character_name, class_pvp, ap, aap, dp, linkgear, updated_at
                FROM gearscore 
                WHERE LOWER(family_name) = LOWER(?)
            ''', (name.strip(),))
            
            result = cursor.fetchone()
            if result:
                results[name.strip().lower()] = result
        
        conn.close()
        return results
    
    # ============================================
    # MÉTODOS PARA EVENTOS E PARTICIPAÇÕES
    # ============================================
    
    def registrar_evento(self, tipo, nome, canal_voz, criado_por, criado_por_nome, participantes):
        """
        Registra um evento e suas participações.
        participantes: lista de dicts com {user_id, family_name, display_name}
        Retorna: (evento_id, quantidade_participantes)
        """
        conn = self.get_connection()
        cursor = conn.cursor()
        
        try:
            # Determinar mês de referência (formato: YYYY-MM)
            from datetime import datetime
            mes_referencia = datetime.now().strftime("%Y-%m")
            
            # Inserir evento
            cursor.execute('''
                INSERT INTO eventos (tipo, nome, canal_voz, criado_por, criado_por_nome, mes_referencia)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (tipo, nome, canal_voz, criado_por, criado_por_nome, mes_referencia))
            
            evento_id = cursor.lastrowid
            
            # Inserir participações
            for p in participantes:
                cursor.execute('''
                    INSERT INTO participacoes (evento_id, user_id, family_name, display_name)
                    VALUES (?, ?, ?, ?)
                ''', (evento_id, p['user_id'], p.get('family_name'), p.get('display_name')))
            
            conn.commit()
            conn.close()
            return evento_id, len(participantes)
        except Exception as e:
            conn.rollback()
            conn.close()
            raise e
    
    def get_relatorio_participacoes(self, mes_referencia=None):
        """
        Retorna relatório de participações do mês.
        Se mes_referencia for None, usa o mês atual.
        Retorna: {
            'eventos_por_tipo': {tipo: quantidade},
            'participacoes_por_player': {user_id: {tipo: quantidade, 'display_name': nome}},
            'total_eventos': int,
            'mes': str
        }
        """
        conn = self.get_connection()
        cursor = conn.cursor()
        
        from datetime import datetime
        if mes_referencia is None:
            mes_referencia = datetime.now().strftime("%Y-%m")
        
        # Contar eventos por tipo
        cursor.execute('''
            SELECT tipo, COUNT(*) as total
            FROM eventos
            WHERE mes_referencia = ?
            GROUP BY tipo
        ''', (mes_referencia,))
        
        eventos_por_tipo = {}
        for row in cursor.fetchall():
            eventos_por_tipo[row[0]] = row[1]
        
        total_eventos = sum(eventos_por_tipo.values())
        
        # Contar participações por player e tipo
        cursor.execute('''
            SELECT p.user_id, p.display_name, p.family_name, e.tipo, COUNT(*) as total
            FROM participacoes p
            JOIN eventos e ON p.evento_id = e.id
            WHERE e.mes_referencia = ?
            GROUP BY p.user_id, e.tipo
        ''', (mes_referencia,))
        
        participacoes_por_player = {}
        for row in cursor.fetchall():
            user_id = row[0]
            display_name = row[1]
            family_name = row[2]
            tipo = row[3]
            quantidade = row[4]
            
            if user_id not in participacoes_por_player:
                participacoes_por_player[user_id] = {
                    'display_name': display_name or family_name or user_id,
                    'family_name': family_name
                }
            participacoes_por_player[user_id][tipo] = quantidade
        
        conn.close()
        
        return {
            'eventos_por_tipo': eventos_por_tipo,
            'participacoes_por_player': participacoes_por_player,
            'total_eventos': total_eventos,
            'mes': mes_referencia
        }
    
    def limpar_eventos_mes_anterior(self):
        """Limpa eventos de meses anteriores ao atual (chamado no dia 1)"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        from datetime import datetime
        mes_atual = datetime.now().strftime("%Y-%m")
        
        try:
            # Deletar participações de eventos antigos
            cursor.execute('''
                DELETE FROM participacoes 
                WHERE evento_id IN (
                    SELECT id FROM eventos WHERE mes_referencia < ?
                )
            ''', (mes_atual,))
            
            # Deletar eventos antigos
            cursor.execute('''
                DELETE FROM eventos WHERE mes_referencia < ?
            ''', (mes_atual,))
            
            deleted = cursor.rowcount
            conn.commit()
            conn.close()
            return deleted
        except Exception as e:
            conn.rollback()
            conn.close()
            raise e

