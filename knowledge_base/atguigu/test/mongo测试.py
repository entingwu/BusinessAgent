from pymongo import MongoClient



myclient = MongoClient("mongodb://192.168.100.100:27017/")
mydb = myclient["atguigu"]
mycol = mydb["users"]

user1 = {"name": "Annie", "age": 100, "sex": "female"}

result = mycol.insert_one(user1)
print(result)