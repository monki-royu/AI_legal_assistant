import os
from dotenv import load_dotenv

from common.path_utils import get_file_path

load_dotenv()
load_dotenv(get_file_path(".env"))
# get_file_path(".env") 只是一个找路标的动作，它只返回一个字符串（比如 /project/.env），它本身不会去读取文件。
#load_dotenv() 才是真正把文件里的配置       加载到系统中      的动作。

class Config:
    #这里负责存放 本项目相关的 所有配置信息，负责 对具体配置文件的读取，存放变量名，后期存放具体取值 只需要 调用.属性即可，
    #因为这里存放了所有对象相关的 配置信息，所以 创建不同的对象，但只需要 调用这一个类，然后.属性即可
    def __init__(self):
        # 大模型相关
        self.MODEL_API_KEY = os.getenv("MODEL_API_KEY")
        #getenv找到文件并将数据---文件里的配置  读取到变量中
        self.MODEL_BASE_URL = os.getenv("MODEL_BASE_URL")
        self.MODEL_NAME = os.getenv("MODEL_NAME")

        # neo4j相关
        self.NEO4J_URI = os.getenv("NEO4J_URI")
        self.NEO4J_USER = os.getenv("NEO4J_USER")
        self.NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")

        # 读取极梦的密钥
        self.JIMENG_AK = os.getenv("JIMENG_AK")
        self.JIMENG_SK = os.getenv("JIMENG_SK")
        #
        # 读取图谱模式层的数据
        self.TCM_METADATA = open(get_file_path("__003__create_neo4j_database/tcm_metadata.json"), "r",
                                 encoding="utf-8").read()
        #
        # # embedding模型
        self.EMBEDDING_MODEL_PATH = os.getenv("EMBEDDING_MODEL_PATH")
        #
        # # index的路径
        self.ENTITY_INDEX_PATH = get_file_path("__003__create_neo4j_database/nero4j_embedding_faiss.index")
        self.ENTITY_ID2TEXT_PATH = get_file_path("__003__create_neo4j_database/nero4j_embedding_faiss_id2text.pkl")
        # # 记忆轮次
        self.history_num = 5


if __name__ == "__main__":
    conf = Config()
    # print(conf.TCM_METADATA)
