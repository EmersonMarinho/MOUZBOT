"""
Versão do banco de dados usando MongoDB Atlas
Instale: pip install pymongo
"""
from pymongo import MongoClient
from datetime import datetime
from config import MONGODB_URI, MONGODB_DB_NAME

class Database:
    def __init__(self):
        self.client = MongoClient(MONGODB_URI)
        self.db = self.client[MONGODB_DB_NAME]
        self.collection = self.db['gearscore']
        self.history_collection = self.db['gearscore_history']
        self.init_database()
    
    def init_database(self):
        """Inicializa o banco de dados criando índices"""
        # Criar índice único para user_id + class_pvp
        self.collection.create_index(
            [("user_id", 1), ("class_pvp", 1)],
            unique=True
        )
        # Índice para histórico
        self.history_collection.create_index(
            [("user_id", 1), ("class_pvp", 1), ("created_at", -1)]
        )
    
    def register_gearscore(self, user_id, family_name, class_pvp, ap, aap, dp, linkgear, character_name=None):
        """Registra um novo gearscore (primeira vez)"""
        # Verificar se já existe registro para esta classe
        existing = self.collection.find_one({
            "user_id": user_id,
            "class_pvp": class_pvp
        })
        
        if existing:
            raise ValueError(f"Você já possui um registro para a classe {class_pvp}. Use /atualizar para modificar.")
        
        # Inserir novo registro
        document = {
            "user_id": user_id,
            "family_name": family_name,
            "character_name": character_name,
            "class_pvp": class_pvp,
            "ap": ap,
            "aap": aap,
            "dp": dp,
            "linkgear": linkgear,
            "updated_at": datetime.utcnow(),
            "is_active": 1
        }
        
        self.collection.insert_one(document)
        
        # Salvar histórico
        total_gs = max(ap, aap) + dp
        history_doc = {
            "user_id": user_id,
            "class_pvp": class_pvp,
            "ap": ap,
            "aap": aap,
            "dp": dp,
            "total_gs": total_gs,
            "created_at": datetime.utcnow()
        }
        self.history_collection.insert_one(history_doc)
    
    def get_user_current_data(self, user_id):
        """Retorna os dados atuais do usuário (family_name, character_name, class_pvp)"""
        result = self.collection.find_one({"user_id": user_id})
        if result:
            return (
                result.get("family_name"),
                result.get("character_name"),
                result.get("class_pvp")
            )
        return None
    
    def update_gearscore(self, user_id, family_name=None, class_pvp=None, ap=None, aap=None, dp=None, linkgear=None, character_name=None):
        """Atualiza o gearscore de um personagem (pode mudar de classe)"""
        # Buscar dados atuais
        current_record = self.collection.find_one({"user_id": user_id})
        if not current_record:
            raise ValueError("Você ainda não possui um registro! Use /registro primeiro.")
        
        current_family_name = current_record.get("family_name")
        current_character_name = current_record.get("character_name")
        current_class_pvp = current_record.get("class_pvp")
        current_ap = current_record.get("ap")
        current_aap = current_record.get("aap")
        current_dp = current_record.get("dp")
        current_linkgear = current_record.get("linkgear")
        
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
        if ap is None:
            ap = current_ap
        if aap is None:
            aap = current_aap
        if dp is None:
            dp = current_dp
        if linkgear is None:
            linkgear = current_linkgear
        
        # Verificar se mudou de classe
        if class_pvp != current_class_pvp:
            # Remover registro da classe antiga
            self.collection.delete_one({
                "user_id": user_id,
                "class_pvp": current_class_pvp
            })
        
        # Atualizar ou inserir gearscore
        document = {
            "user_id": user_id,
            "family_name": family_name,
            "character_name": character_name,
            "class_pvp": class_pvp,
            "ap": ap,
            "aap": aap,
            "dp": dp,
            "linkgear": linkgear,
            "updated_at": datetime.utcnow()
        }
        # Manter is_active se já existir, senão definir como 1
        existing = self.collection.find_one({"user_id": user_id, "class_pvp": class_pvp})
        if existing and "is_active" in existing:
            document["is_active"] = existing["is_active"]
        else:
            document["is_active"] = 1
        
        self.collection.update_one(
            {"user_id": user_id, "class_pvp": class_pvp},
            {"$set": document},
            upsert=True
        )
        
        # Salvar histórico
        total_gs = max(ap, aap) + dp
        history_doc = {
            "user_id": user_id,
            "class_pvp": class_pvp,
            "ap": ap,
            "aap": aap,
            "dp": dp,
            "total_gs": total_gs,
            "created_at": datetime.utcnow()
        }
        self.history_collection.insert_one(history_doc)
    
    def get_gearscore(self, user_id, class_pvp=None):
        """Busca o gearscore de um usuário"""
        if class_pvp:
            result = self.collection.find_one({
                "user_id": user_id,
                "class_pvp": class_pvp
            })
            return [result] if result else []
        else:
            results = list(self.collection.find(
                {"user_id": user_id}
            ).sort("updated_at", -1))
            return results
    
    def get_user_current_class(self, user_id):
        """Retorna a classe atual do usuário"""
        result = self.collection.find_one({"user_id": user_id})
        return result.get("class_pvp") if result else None
    
    def get_all_gearscores(self, valid_user_ids=None):
        """
        Busca todos os gearscores
        
        Args:
            valid_user_ids: Set ou lista de user_ids válidos (que têm o cargo da guilda).
                           Se None, retorna todos os registros.
        """
        query = {}
        if valid_user_ids:
            query = {"user_id": {"$in": list(valid_user_ids)}}
        
        results = list(self.collection.find(query).sort("updated_at", -1))
        return results
    
    def get_class_statistics(self, valid_user_ids=None):
        """
        Retorna estatísticas por classe
        
        Args:
            valid_user_ids: Set ou lista de user_ids válidos (que têm o cargo da guilda).
                           Se None, retorna todos os registros.
        """
        pipeline = []
        
        # Adicionar filtro de user_ids se fornecido
        if valid_user_ids:
            pipeline.append({
                "$match": {"user_id": {"$in": list(valid_user_ids)}}
            })
        
        pipeline.extend([
            {
                "$addFields": {
                    "gs": {
                        "$add": [
                            {"$max": ["$ap", "$aap"]},
                            "$dp"
                        ]
                    }
                }
            },
            {
                "$group": {
                    "_id": "$class_pvp",
                    "total": {"$sum": 1},
                    "avg_gs": {"$avg": "$gs"},
                    "avg_ap": {"$avg": "$ap"},
                    "avg_aap": {"$avg": "$aap"},
                    "avg_dp": {"$avg": "$dp"}
                }
            },
            {
                "$project": {
                    "class_pvp": "$_id",
                    "total": 1,
                    "avg_gs": 1,
                    "avg_ap": 1,
                    "avg_aap": 1,
                    "avg_dp": 1,
                    "_id": 0
                }
            },
            {"$sort": {"total": -1, "avg_gs": -1}}
        ])
        
        results = list(self.collection.aggregate(pipeline))
        return results
    
    def get_class_members(self, class_pvp, valid_user_ids=None):
        """
        Retorna todos os membros de uma classe específica
        
        Args:
            class_pvp: Nome da classe
            valid_user_ids: Set ou lista de user_ids válidos (que têm o cargo da guilda).
                           Se None, retorna todos os registros.
        """
        # Ordenar por GS (MAX(AP, AAP) + DP)
        match_conditions = {"class_pvp": class_pvp}
        
        if valid_user_ids:
            match_conditions["user_id"] = {"$in": list(valid_user_ids)}
        
        pipeline = [
            {"$match": match_conditions},
            {
                "$addFields": {
                    "gs": {
                        "$add": [
                            {"$max": ["$ap", "$aap"]},
                            "$dp"
                        ]
                    }
                }
            },
            {"$sort": {"gs": -1}}
        ]
        results = list(self.collection.aggregate(pipeline))
        # Remover campo calculado antes de retornar
        for result in results:
            result.pop('gs', None)
        return results
    
    def get_user_history(self, user_id, class_pvp=None):
        """Retorna histórico de progressão de um usuário"""
        query = {"user_id": user_id}
        if class_pvp:
            query["class_pvp"] = class_pvp
        
        results = list(self.history_collection.find(query).sort("created_at", -1).limit(50))
        return results
    
    def get_user_progress(self, user_id, class_pvp):
        """Calcula progressão de um personagem"""
        pipeline = [
            {"$match": {"user_id": user_id, "class_pvp": class_pvp}},
            {
                "$group": {
                    "_id": None,
                    "first_gs": {"$min": "$total_gs"},
                    "current_gs": {"$max": "$total_gs"},
                    "updates": {"$sum": 1},
                    "first_update": {"$min": "$created_at"},
                    "last_update": {"$max": "$created_at"}
                }
            },
            {
                "$project": {
                    "first_gs": 1,
                    "current_gs": 1,
                    "progress": {"$subtract": ["$current_gs", "$first_gs"]},
                    "updates": 1,
                    "first_update": 1,
                    "last_update": 1,
                    "_id": 0
                }
            }
        ]
        result = list(self.history_collection.aggregate(pipeline))
        return result[0] if result else None
    
    def get_user_history(self, user_id, character_name=None):
        """Retorna histórico de progressão de um usuário"""
        query = {"user_id": user_id}
        if character_name:
            query["character_name"] = character_name
        
        results = list(self.history_collection.find(query).sort("created_at", -1).limit(50))
        return results
    
    def get_user_progress(self, user_id, character_name):
        """Calcula progressão de um personagem"""
        pipeline = [
            {"$match": {"user_id": user_id, "character_name": character_name}},
            {
                "$group": {
                    "_id": None,
                    "first_gs": {"$min": "$total_gs"},
                    "current_gs": {"$max": "$total_gs"},
                    "updates": {"$sum": 1},
                    "first_update": {"$min": "$created_at"},
                    "last_update": {"$max": "$created_at"}
                }
            },
            {
                "$project": {
                    "first_gs": 1,
                    "current_gs": 1,
                    "progress": {"$subtract": ["$current_gs", "$first_gs"]},
                    "updates": 1,
                    "first_update": 1,
                    "last_update": 1,
                    "_id": 0
                }
            }
        ]
        result = list(self.history_collection.aggregate(pipeline))
        return result[0] if result else None
    
    def clear_all_data(self):
        """Limpa todos os dados do banco (gearscore e histórico)"""
        try:
            # Limpar histórico
            self.history_collection.delete_many({})
            # Limpar gearscore
            self.collection.delete_many({})
            return True, "Todos os dados foram limpos com sucesso!"
        except Exception as e:
            return False, f"Erro ao limpar banco de dados: {str(e)}"
    
    def clear_history_only(self):
        """Limpa apenas o histórico, mantendo os gearscores atuais"""
        try:
            result = self.history_collection.delete_many({})
            deleted_count = result.deleted_count
            return True, f"Histórico limpo! {deleted_count} registro(s) removido(s)."
        except Exception as e:
            return False, f"Erro ao limpar histórico: {str(e)}"
    
    def close(self):
        """Fecha a conexão com o banco de dados"""
        self.client.close()

