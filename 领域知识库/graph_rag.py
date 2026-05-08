import os
import logging
import dotenv
from langchain.prompts import PromptTemplate
from langchain_community.graphs import Neo4jGraph
from langchain_community.llms import Ollama
from langchain.chains import GraphCypherQAChain

# 加载环境变量
dotenv.load_dotenv()

# 配置日志
logging.basicConfig(level=logging.INFO)
logging.info('Starting up the Knowledge Graph RAG...')

# 初始化 Neo4j 连接
logging.info(f'Instantiating the Neo4J connector for: {os.getenv("NEO4J_URI")}')
graph = Neo4jGraph(
    url=os.getenv("NEO4J_URI"),
    username=os.getenv("NEO4J_USERNAME"),
    password=os.getenv("NEO4J_PASSWORD")
)

# 初始化 LLM（llama3）
logging.info('Instantiating LLM to use with the LLMGraphTransformer')
llm = Ollama(model='llama3', temperature=0.0)

# 自定义 Cypher 生成提示词（核心修改：强制使用 Person.id 属性）
CYPHER_GENERATION_TEMPLATE = """
You are an expert in Neo4j Cypher queries.
Given the following graph schema, write a Cypher query to answer the user's question.

Schema:
{schema}

Important rules you MUST follow:
1. The "Person" node uses the "id" property to store identifiers (e.g., names like "Carlos Pereira").
2. When matching a "Person" node, ALWAYS use the "id" property (example: (p:Person {{id: "Carlos Pereira"}})).
3. NEVER use the "name" property for "Person" nodes (it does not exist in the database).
4. Only return the Cypher query, with no additional text, explanations, or formatting.

User question: {question}
Cypher query:
"""

CYPHER_GENERATION_PROMPT = PromptTemplate(
    input_variables=["schema", "question"],
    template=CYPHER_GENERATION_TEMPLATE
)

# 初始化 GraphCypherQAChain 并应用自定义提示词
logging.info('Initializing GraphCypherQAChain with custom prompts...')
chain = GraphCypherQAChain.from_llm(
    graph=graph,
    llm=llm,
    verbose=True,
    allow_dangerous_requests=True,
    cypher_prompt=CYPHER_GENERATION_PROMPT  # 应用自定义提示词
)

logging.info('Knowledge Graph RAG is ready to go!')
logging.info('='*50)

def main():
    logging.info('Type "exit" to quit the program.')
    while True:
        question = input('\nAsk me a question: ')
        if question.lower() == 'exit':
            break
        try:
            result = chain.invoke({"query": question})
            if result.get('result'):
                print(f"Answer: {result['result']}")
            else:
                print("No results found.")
        except Exception as e:
            print(f"An error occurred: {str(e)}")

if __name__ == '__main__':
    main()