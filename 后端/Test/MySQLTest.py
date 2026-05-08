"""
=============================================================================
文件名称：MySQLTest.py
作用：用于前期测试系统 MySQL 数据库客户端 mysql-connector-python 环境安装是否齐全、
服务器端口连接情况，以及执行新建、建立 Schema 以及增删行的基础演练。
适用系统：Windows 10/11
测试环境：Python 3.10.x
前提条件：必须有一个本地的 MySQL 允许使用 root/1234 登录在 3306 默认端口。
=============================================================================
"""
import mysql.connector

def connect_to_database():
    """
    第一阶段检测：
    连入系统默认自带字典数据库 `information_schema` 以确保最基本的连通性和凭证正确性，
    随后会以列表的形式印出里面的 Tables 表单作为确认存活的证明。
    """
    try:
        # 连接到MySQL数据库（使用最基础配置）
        connection = mysql.connector.connect(
            host="localhost",  # 数据库主机地址
            user="root",       # 数据库用户名
            password="1234",   # 数据库密码
            database="information_schema"  # 指定数据库
        )

        if connection.is_connected():
            print("成功连接到数据库！")

            # 获取 information_schema 的表内容
            cursor = connection.cursor()
            cursor.execute("SHOW TABLES")

            print("information_schema 中的表：")
            for table in cursor:
                print(table[0])

    except mysql.connector.Error as err:
        print(f"连接失败: {err}")

    finally:
        # 清理关闭：不论运行成功与失败都保障连接不会被遗留占用
        if 'connection' in locals() and connection.is_connected():
            cursor.close()
            connection.close()
            print("数据库连接已关闭。")

def create_test_database():
    """
    第二阶段检测：
    通过 root 不带库名的裸连方式，测验 `CREATE DATABASE` 权限，以初始化目标库 "Test"。
    这对应日后应用初始化 chatbot 库的逻辑。
    """
    try:
        # 连接到 MySQL 宿主机（不选定 Database 操作）
        connection = mysql.connector.connect(
            host="localhost",  # 数据库主机地址
            user="root",       # 数据库用户名
            password="1234"    # 数据库密码
        )

        if connection.is_connected():
            print("成功连接到数据库！")

            # 创建专属测试数据库 Test
            cursor = connection.cursor()
            cursor.execute("CREATE DATABASE IF NOT EXISTS Test")
            print("Test数据库已创建或已存在。")

    except mysql.connector.Error as err:
        print(f"操作失败: {err}")

    finally:
        if 'connection' in locals() and connection.is_connected():
            cursor.close()
            connection.close()
            print("数据库连接已关闭。")

def create_example_table_and_insert_data():
    """
    第三阶段检测：
    通过选定先前建立好的 Test 数据库，建构测试表 "Example" 
    并写入单行含有具体姓名和主键 ID 的样本数据，最后 SELECT 返回终端以便证明 MySQL 库、表及增用过程健康连贯。
    """
    try:
        # 使用特定 Database 的全功能直连
        connection = mysql.connector.connect(
            host="localhost",  # 数据库主机地址
            user="root",       # 数据库用户名
            password="1234",   # 数据库密码
            database="Test"    # 指定挂载 Test 数据库
        )

        if connection.is_connected():
            print("成功连接到 Test 数据库！")

            # 建立支持自增键的新 Example 表对象
            cursor = connection.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS Example (
                    ID INT AUTO_INCREMENT PRIMARY KEY,
                    Name VARCHAR(255) NOT NULL
                )
                """
            )
            print("Example表已创建或已存在。")

            # 插入或覆盖更新数据（通过 ON DUPLICATE 可以避免重复运行抛挂）
            cursor.execute("INSERT INTO Example (ID, Name) VALUES (%s, %s) ON DUPLICATE KEY UPDATE Name=VALUES(Name)", (1, "Joey"))
            connection.commit()
            print("数据已插入：ID=1, Name=Joey")

            # 最终读取并打印该 Example 表内容
            cursor.execute("SELECT * FROM Example")
            rows = cursor.fetchall()

            print("Example表内容：")
            for row in rows:
                print(row)

    except mysql.connector.Error as err:
        print(f"操作失败: {err}")

    finally:
        if 'connection' in locals() and connection.is_connected():
            cursor.close()
            connection.close()
            print("数据库连接已关闭。")

# 当本脚本作为主程序被调用时依次往下运行全部测试连贯任务
if __name__ == "__main__":
    connect_to_database()
    create_test_database()
    create_example_table_and_insert_data()