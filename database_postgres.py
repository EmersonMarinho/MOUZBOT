"""
Versão do banco de dados usando PostgreSQL (Supabase, Railway, Neon, etc.)
Instale: pip install psycopg2-binary
"""
import psycopg2
import os
from config import DATABASE_URL

class Database:
    def __init__(self):
        self.db_url = DATABASE_URL
        self.init_database()
    
    def get_connection(self):
        """Retorna uma conexão com o banco de dados PostgreSQL"""
        return psycopg2.connect(self.db_url)
    
    def init_database(self):
        """Inicializa o banco de dados criando a tabela se não existir"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        # Verificar e migrar tabela gearscore se necessário
        cursor.execute('''
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name = 'gearscore' AND column_name = 'character_name'
        ''')
        has_character_name = cursor.fetchone()
        
        if has_character_name:
            # Migrar tabela gearscore: remover character_name, adicionar constraint nova
            try:
                # Dropar constraint antiga se existir
                cursor.execute('''
                    ALTER TABLE gearscore 
                    DROP CONSTRAINT IF EXISTS gearscore_user_id_character_name_key
                ''')
                
                # Adicionar coluna class_pvp se não existir (pode ter sido criada parcialmente)
                cursor.execute('''
                    SELECT column_name 
                    FROM information_schema.columns 
                    WHERE table_name = 'gearscore' AND column_name = 'class_pvp'
                ''')
                if not cursor.fetchone():
                    cursor.execute('''
                        ALTER TABLE gearscore 
                        ADD COLUMN class_pvp TEXT
                    ''')
                    # Atualizar class_pvp com valor padrão se necessário
                    cursor.execute('''
                        UPDATE gearscore 
                        SET class_pvp = 'Unknown' 
                        WHERE class_pvp IS NULL
                    ''')
                    cursor.execute('''
                        ALTER TABLE gearscore 
                        ALTER COLUMN class_pvp SET NOT NULL
                    ''')
                
                # Remover coluna character_name
                cursor.execute('''
                    ALTER TABLE gearscore 
                    DROP COLUMN IF EXISTS character_name
                ''')
                
                # Adicionar constraint nova
                cursor.execute('''
                    ALTER TABLE gearscore 
                    ADD CONSTRAINT gearscore_user_id_class_pvp_key 
                    UNIQUE (user_id, class_pvp)
                ''')
            except Exception as e:
                print(f"Aviso na migração de gearscore: {e}")
        
        # Criar tabela gearscore com estrutura nova
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS gearscore (
                id SERIAL PRIMARY KEY,
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
        
        # Verificar se tabela gearscore_history existe
        cursor.execute('''
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_name = 'gearscore_history'
            )
        ''')
        history_table_exists = cursor.fetchone()[0]
        
        if history_table_exists:
            # Verificar estrutura da tabela existente
            cursor.execute('''
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name = 'gearscore_history' AND column_name = 'character_name'
            ''')
            has_history_character_name = cursor.fetchone()
            
            cursor.execute('''
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name = 'gearscore_history' AND column_name = 'class_pvp'
            ''')
            has_history_class_pvp = cursor.fetchone()
            
            if has_history_character_name and not has_history_class_pvp:
                # Migrar tabela gearscore_history: estrutura antiga
                try:
                    # Adicionar coluna class_pvp
                    cursor.execute('''
                        ALTER TABLE gearscore_history 
                        ADD COLUMN class_pvp TEXT DEFAULT 'Unknown'
                    ''')
                    cursor.execute('''
                        ALTER TABLE gearscore_history 
                        ALTER COLUMN class_pvp SET NOT NULL
                    ''')
                    cursor.execute('''
                        ALTER TABLE gearscore_history 
                        ALTER COLUMN class_pvp DROP DEFAULT
                    ''')
                    
                    # Remover coluna character_name
                    cursor.execute('''
                        ALTER TABLE gearscore_history 
                        DROP COLUMN IF EXISTS character_name
                    ''')
                    
                    # Dropar índice antigo se existir
                    try:
                        cursor.execute('''
                            DROP INDEX IF EXISTS idx_history_user_char
                        ''')
                    except:
                        pass
                except Exception as e:
                    print(f"Aviso na migração de gearscore_history: {e}")
        else:
            # Criar tabela gearscore_history com estrutura nova
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS gearscore_history (
                    id SERIAL PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    class_pvp TEXT NOT NULL,
                    ap INTEGER NOT NULL,
                    aap INTEGER NOT NULL,
                    dp INTEGER NOT NULL,
                    total_gs INTEGER NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
        
        # Criar índice (só se a coluna class_pvp existir)
        try:
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_history_user_class 
                ON gearscore_history(user_id, class_pvp, created_at)
            ''')
        except Exception as e:
            print(f"Aviso ao criar índice: {e}")
        
        conn.commit()
        cursor.close()
        conn.close()
    
    def register_gearscore(self, user_id, family_name, class_pvp, ap, aap, dp, linkgear):
        """Registra um novo gearscore (primeira vez)"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        # Verificar se já existe registro para esta classe
        cursor.execute('''
            SELECT class_pvp FROM gearscore 
            WHERE user_id = %s AND class_pvp = %s
        ''', (user_id, class_pvp))
        existing = cursor.fetchone()
        
        if existing:
            conn.close()
            raise ValueError(f"Você já possui um registro para a classe {class_pvp}. Use /atualizar para modificar.")
        
        # Inserir novo registro
        cursor.execute('''
            INSERT INTO gearscore 
            (user_id, family_name, class_pvp, ap, aap, dp, linkgear, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
        ''', (user_id, family_name, class_pvp, ap, aap, dp, linkgear))
        
        # Salvar histórico
        total_gs = max(ap, aap) + dp
        try:
            cursor.execute('''
                INSERT INTO gearscore_history 
                (user_id, class_pvp, ap, aap, dp, total_gs, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
            ''', (user_id, class_pvp, ap, aap, dp, total_gs))
        except Exception as e:
            print(f"⚠️ Erro ao salvar histórico no register: {e}")
            # Não falhar o registro se o histórico falhar
        
        conn.commit()
        cursor.close()
        conn.close()
    
    def update_gearscore(self, user_id, family_name, class_pvp, ap, aap, dp, linkgear):
        """Atualiza o gearscore de um personagem (pode mudar de classe)"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        # Verificar se já existe registro para esta classe
        cursor.execute('''
            SELECT class_pvp FROM gearscore 
            WHERE user_id = %s AND class_pvp = %s
        ''', (user_id, class_pvp))
        existing_same_class = cursor.fetchone()
        
        # Se mudou de classe, remover registro da classe antiga
        if not existing_same_class:
            # Buscar classe antiga
            cursor.execute('''
                SELECT class_pvp FROM gearscore 
                WHERE user_id = %s
            ''', (user_id,))
            old_class = cursor.fetchone()
            
            if old_class:
                # Remover registro da classe antiga
                cursor.execute('''
                    DELETE FROM gearscore 
                    WHERE user_id = %s AND class_pvp = %s
                ''', (user_id, old_class[0]))
        
        # Atualizar ou inserir gearscore
        cursor.execute('''
            INSERT INTO gearscore 
            (user_id, family_name, class_pvp, ap, aap, dp, linkgear, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (user_id, class_pvp) 
            DO UPDATE SET 
                family_name = EXCLUDED.family_name,
                ap = EXCLUDED.ap,
                aap = EXCLUDED.aap,
                dp = EXCLUDED.dp,
                linkgear = EXCLUDED.linkgear,
                updated_at = CURRENT_TIMESTAMP
        ''', (user_id, family_name, class_pvp, ap, aap, dp, linkgear))
        
        # Salvar histórico
        total_gs = max(ap, aap) + dp
        try:
            cursor.execute('''
                INSERT INTO gearscore_history 
                (user_id, class_pvp, ap, aap, dp, total_gs, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
            ''', (user_id, class_pvp, ap, aap, dp, total_gs))
        except Exception as e:
            print(f"⚠️ Erro ao salvar histórico no update: {e}")
            # Não falhar a atualização se o histórico falhar
        
        conn.commit()
        cursor.close()
        conn.close()
    
    def get_gearscore(self, user_id, class_pvp=None):
        """Busca o gearscore de um usuário"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        if class_pvp:
            cursor.execute('''
                SELECT id, user_id, family_name, class_pvp, ap, aap, dp, linkgear, updated_at
                FROM gearscore 
                WHERE user_id = %s AND class_pvp = %s
            ''', (user_id, class_pvp))
        else:
            cursor.execute('''
                SELECT id, user_id, family_name, class_pvp, ap, aap, dp, linkgear, updated_at
                FROM gearscore 
                WHERE user_id = %s
                ORDER BY updated_at DESC
            ''', (user_id,))
        
        result = cursor.fetchall()
        cursor.close()
        conn.close()
        return result
    
    def get_user_current_class(self, user_id):
        """Retorna a classe atual do usuário"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT class_pvp FROM gearscore 
            WHERE user_id = %s
            LIMIT 1
        ''', (user_id,))
        
        result = cursor.fetchone()
        cursor.close()
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
            placeholders = ','.join(['%s'] * len(valid_user_ids))
            query = f'''
                SELECT id, user_id, family_name, class_pvp, ap, aap, dp, linkgear, updated_at
                FROM gearscore 
                WHERE user_id IN ({placeholders})
                ORDER BY updated_at DESC
            '''
            cursor.execute(query, list(valid_user_ids))
        else:
            cursor.execute('''
                SELECT id, user_id, family_name, class_pvp, ap, aap, dp, linkgear, updated_at
                FROM gearscore 
                ORDER BY updated_at DESC
            ''')
        
        result = cursor.fetchall()
        cursor.close()
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
            placeholders = ','.join(['%s'] * len(valid_user_ids))
            query = f'''
                SELECT 
                    class_pvp,
                    COUNT(*) as total,
                    AVG(GREATEST(ap, aap) + dp) as avg_gs,
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
                    AVG(GREATEST(ap, aap) + dp) as avg_gs,
                    AVG(ap) as avg_ap,
                    AVG(aap) as avg_aap,
                    AVG(dp) as avg_dp
                FROM gearscore
                GROUP BY class_pvp
                ORDER BY total DESC, avg_gs DESC
            ''')
        
        result = cursor.fetchall()
        cursor.close()
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
            placeholders = ','.join(['%s'] * len(valid_user_ids))
            query = f'''
                SELECT id, user_id, family_name, class_pvp, ap, aap, dp, linkgear, updated_at
                FROM gearscore 
                WHERE class_pvp = %s AND user_id IN ({placeholders})
                ORDER BY (GREATEST(ap, aap) + dp) DESC
            '''
            cursor.execute(query, [class_pvp] + list(valid_user_ids))
        else:
            cursor.execute('''
                SELECT id, user_id, family_name, class_pvp, ap, aap, dp, linkgear, updated_at
                FROM gearscore 
                WHERE class_pvp = %s
                ORDER BY (GREATEST(ap, aap) + dp) DESC
            ''', (class_pvp,))
        
        result = cursor.fetchall()
        cursor.close()
        conn.close()
        return result
    
    def get_user_history(self, user_id, class_pvp=None):
        """Retorna histórico de progressão de um usuário"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        try:
            if class_pvp:
                cursor.execute('''
                    SELECT ap, aap, dp, total_gs, created_at
                    FROM gearscore_history
                    WHERE user_id = %s AND class_pvp = %s
                    ORDER BY created_at DESC
                    LIMIT 50
                ''', (user_id, class_pvp))
            else:
                cursor.execute('''
                    SELECT class_pvp, ap, aap, dp, total_gs, created_at
                    FROM gearscore_history
                    WHERE user_id = %s
                    ORDER BY created_at DESC
                    LIMIT 50
                ''', (user_id,))
            
            result = cursor.fetchall()
        except Exception as e:
            print(f"⚠️ Erro ao buscar histórico: {e}")
            # Tentar verificar se a tabela existe e tem a estrutura correta
            try:
                cursor.execute('''
                    SELECT column_name 
                    FROM information_schema.columns 
                    WHERE table_name = 'gearscore_history'
                ''')
                columns = [row[0] for row in cursor.fetchall()]
                print(f"Colunas na tabela gearscore_history: {columns}")
            except:
                pass
            result = []
        finally:
            cursor.close()
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
            WHERE user_id = %s AND class_pvp = %s
        ''', (user_id, class_pvp))
        
        result = cursor.fetchone()
        cursor.close()
        conn.close()
        return result
    
    def clear_all_data(self):
        """Limpa todos os dados do banco (gearscore e histórico)"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        try:
            # Limpar histórico primeiro (devido a foreign keys se houver)
            cursor.execute('DELETE FROM gearscore_history')
            # Limpar gearscore
            cursor.execute('DELETE FROM gearscore')
            
            conn.commit()
            
            cursor.close()
            conn.close()
            
            return True, "Todos os dados foram limpos com sucesso!"
        except Exception as e:
            conn.rollback()
            cursor.close()
            conn.close()
            return False, f"Erro ao limpar banco de dados: {str(e)}"
    
    def clear_history_only(self):
        """Limpa apenas o histórico, mantendo os gearscores atuais"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        try:
            cursor.execute('DELETE FROM gearscore_history')
            deleted_count = cursor.rowcount if hasattr(cursor, 'rowcount') else 0
            
            conn.commit()
            cursor.close()
            conn.close()
            
            return True, f"Histórico limpo! {deleted_count} registro(s) removido(s)."
        except Exception as e:
            conn.rollback()
            cursor.close()
            conn.close()
            return False, f"Erro ao limpar histórico: {str(e)}"

