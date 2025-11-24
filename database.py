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
                class_pvp TEXT NOT NULL,
                ap INTEGER NOT NULL,
                aap INTEGER NOT NULL,
                dp INTEGER NOT NULL,
                linkgear TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, class_pvp)
            )
        ''')
        
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
        
        conn.commit()
        conn.close()
    
    def register_gearscore(self, user_id, family_name, class_pvp, ap, aap, dp, linkgear):
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
            (user_id, family_name, class_pvp, ap, aap, dp, linkgear, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ''', (user_id, family_name, class_pvp, ap, aap, dp, linkgear))
        
        # Salvar histórico
        total_gs = max(ap, aap) + dp
        cursor.execute('''
            INSERT INTO gearscore_history 
            (user_id, class_pvp, ap, aap, dp, total_gs, created_at)
            VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ''', (user_id, class_pvp, ap, aap, dp, total_gs))
        
        conn.commit()
        conn.close()
    
    def update_gearscore(self, user_id, family_name, class_pvp, ap, aap, dp, linkgear):
        """Atualiza o gearscore de um personagem (pode mudar de classe)"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        # Verificar se já existe registro para esta classe
        cursor.execute('''
            SELECT class_pvp FROM gearscore 
            WHERE user_id = ? AND class_pvp = ?
        ''', (user_id, class_pvp))
        existing_same_class = cursor.fetchone()
        
        # Se mudou de classe, remover registro da classe antiga
        if not existing_same_class:
            # Buscar classe antiga
            cursor.execute('''
                SELECT class_pvp FROM gearscore 
                WHERE user_id = ?
            ''', (user_id,))
            old_class = cursor.fetchone()
            
            if old_class:
                # Remover registro da classe antiga
                cursor.execute('''
                    DELETE FROM gearscore 
                    WHERE user_id = ? AND class_pvp = ?
                ''', (user_id, old_class[0]))
        
        # Atualizar ou inserir gearscore
        cursor.execute('''
            INSERT OR REPLACE INTO gearscore 
            (user_id, family_name, class_pvp, ap, aap, dp, linkgear, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ''', (user_id, family_name, class_pvp, ap, aap, dp, linkgear))
        
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
        
        if class_pvp:
            cursor.execute('''
                SELECT * FROM gearscore 
                WHERE user_id = ? AND class_pvp = ?
            ''', (user_id, class_pvp))
        else:
            cursor.execute('''
                SELECT * FROM gearscore 
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
        
        if valid_user_ids:
            placeholders = ','.join(['?'] * len(valid_user_ids))
            query = f'''
                SELECT * FROM gearscore 
                WHERE user_id IN ({placeholders})
                ORDER BY updated_at DESC
            '''
            cursor.execute(query, list(valid_user_ids))
        else:
            cursor.execute('''
                SELECT * FROM gearscore 
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
        
        if valid_user_ids:
            placeholders = ','.join(['?'] * len(valid_user_ids))
            query = f'''
                SELECT * FROM gearscore 
                WHERE class_pvp = ? AND user_id IN ({placeholders})
                ORDER BY (CASE WHEN ap > aap THEN ap ELSE aap END + dp) DESC
            '''
            cursor.execute(query, [class_pvp] + list(valid_user_ids))
        else:
            cursor.execute('''
                SELECT * FROM gearscore 
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

