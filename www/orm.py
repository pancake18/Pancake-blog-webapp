import logging, asyncio, aiomysql

# 打印SQL语句日志
def log(sql, args=()):
    logging.info('SQL: %s' % sql)

# 创建全局数据库连接池，由全局变量__pool存储，每个http请求都从池中获得数据库连接
# 缺省情况下编码设置为utf-8，自动提交事务
async def create_pool(loop, **kw):  # 传入事件循环对象loop
    logging.info('create database connection pool...')
    global __pool
    __pool = await aiomysql.create_pool(
        # 连接所需参数
        host=kw.get('host', 'localhost'),
        port=kw.get('port', 3306),
        user=kw['user'],
        password=kw['password'],
        db=kw['db'],
        charset=kw.get('charset', 'utf8'),
        autocommit=kw.get('autocommit', True),
        maxsize=kw.get('maxsize', 10),
        minsize=kw.get('minsize', 1),
        loop=loop
    )

# 封装select语句
async def select(sql, args, size=None):
    log(sql, args)
    global __pool  # 引入全局变量__pool
    with (await __pool) as conn:  # 从连接池获取一个连接
        cur = await conn.cursor(aiomysql.DictCursor)  # 打开游标
        # 执行MySQL语句，SQL语句的占位符是'?',而MySQL的占位符是'%s',需要进行转换
        await cur.execute(sql.replace('?', '%s'), args or ())
        if size:
            rs = await cur.fetchmany(size)
        else:
            rs = await cur.fetchall()  # 拿到结果集，结果集是一个list，每个元素是一个tuple，对应数据库一行记录。
        await cur.close()  # 关闭游标
        logging.info('rows returned: %s' % len(rs))
        return rs

# 封装insert,update,delete语句
async def execute(sql, args):
    log(sql)
    with (await __pool) as conn:
        try:
            cur = await conn.cursor()
            await cur.execute(sql.replace('?', '%s'), args)
            # 影响的行数
            affected = cur.rowcount
            await cur.close()
        except BaseException as e:
            raise
        return affected

# ORM框架
# 构造SQL语句占位符'?'
def create_args_string(num):
    L = []
    for n in range(num):
        L.append('?')
    return ', '.join(L)

# 元类
# 任何继承自Model的类（比如User，一个类即对应数据库一个表，表的每个字段对应类里面的一个Field对象，Field对象会有
# name,column_type,,primary_key,default四个属性），会自动通过ModelMetaclass扫描映射关系，类原有的属性会被删掉
# 并存储到自身的类属性，如__table__、__mappings__中
class ModelMetaclass(type):

    def __new__(cls, name, bases, attrs):  # 当前准备创建的类，类名， 父类，类的属性和方法的字典
        if name == 'Model':  # 排除对Model类的修改:
            return type.__new__(cls, name, bases, attrs)
        tableName = attrs.get('__table__', None) or name  # 获取__table__属性,没有则设置为类名name
        logging.info('found model: %s (table: %s)' % (name, tableName))
        mappings = dict()  # 保存类属性和列的映射关系到mappings字典
        fields = []  # 保存除主键外的字段
        primaryKey = None  # 保存主键
        for k, v in attrs.items():
            if isinstance(v, Field):
                logging.info('  found mapping: %s ==> %s' % (k, v))
                mappings[k] = v
                if v.primary_key:  # 找到一个主键
                    if primaryKey:  # 若之前已经找到主键，抛出异常
                        raise RuntimeError('Duplicate primary key for field: %s' % k)
                    primaryKey = k
                else:
                    fields.append(k)  # 不是主键则加入fields列表
        if not primaryKey:  # 若没有找到主键,抛出异常
            raise RuntimeError('Primary key not found.')
        for k in mappings.keys():  # 删除类属性中的Field，否则容易造成运行错误，实例属性会掩盖到类的同名属性
            attrs.pop(k)
        escaped_fields = list(map(lambda f: '`%s`' % f, fields))  # 给fields中给各字段名加上``，避免与MySQL关键字起冲突
        # 给创建的类添加属性
        attrs['__mappings__'] = mappings  # 保存属性和列的映射关系
        attrs['__table__'] = tableName  # 保存表名
        attrs['__primary_key__'] = primaryKey  # 主键属性名
        attrs['__fields__'] = fields  # 除主键外的属性名
        # 构造默认的SELECT, INSERT, UPDATE和DELETE语句:
        attrs['__select__'] = 'select `%s`, %s from `%s`' % (primaryKey, ', '.join(escaped_fields), tableName)
        attrs['__insert__'] = 'insert into `%s` (%s, `%s`) values (%s)' % (tableName, ', '.join(escaped_fields), primaryKey, create_args_string(len(escaped_fields) + 1))
        attrs['__update__'] = 'update `%s` set %s where `%s`=?' % (tableName, ', '.join(map(lambda f: '`%s`=?' % (mappings.get(f).name or f), fields)), primaryKey)
        attrs['__delete__'] = 'delete from `%s` where `%s`=?' % (tableName, primaryKey)
        return type.__new__(cls, name, bases, attrs)

# 所有ORM映射的基类Model，封装了查找（类方法），插入，更新，删除（实例方法）等接口
class Model(dict, metaclass = ModelMetaclass) :

    def __init__(self, **kw):
        super(Model, self).__init__(**kw)

    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            raise AttributeError(r"'Model' object has no attribute '%s'" % key)

    def __setattr__(self, key, value):
        self[key] = value

    def getValue(self, key):
        return getattr(self, key, None)

    def getValueOrDefault(self, key):
        value = getattr(self, key, None)
        if value is None:
            field = self.__mappings__[key]
            if field.default is not None:
                value = field.default() if callable(field.default) else field.default
                logging.debug('using default value for %s: %s' % (key, str(value)))
                setattr(self, key, value)
        return value

    # 定义class方法用于查找
    # 查找全部列
    @classmethod
    async def findAll(cls, where=None, args=None, **kw):  # cls：当前调用此方法的类
        # find objects by where clause
        sql = [cls.__select__]  # 默认select语句
        if where:
            sql.append('where')
            sql.append(where)
        if args is None:
            args = []
        orderBy = kw.get('orderBy', None)
        if orderBy:
            sql.append('order by')
            sql.append(orderBy)
        limit = kw.get('limit', None)
        if limit is not None:
            sql.append('limit')
            if isinstance(limit, int):
                sql.append('?')
                args.append(limit)
            elif isinstance(limit, tuple) and len(limit) == 2:
                sql.append('?, ?')
                args.extend(limit)
            else:
                raise ValueError('Invalid limit value: %s' % str(limit))
        # 异步执行select函数
        rs = await select(' '.join(sql), args)
        # 返回结果
        return [cls(**r) for r in rs]

    @classmethod
    async def findNumber(cls, selectField, where=None, args=None):  # 查找符合条件的记录条数
        # find number by select and where
        # 构造SQL语句
        sql = ['select %s _num_ from `%s`' % (selectField, cls.__table__)]  # _num_:别名
        if where:
            sql.append('where')
            sql.append(where)
        rs = await select(' '.join(sql), args, 1)
        if len(rs) == 0:
            return None
        return rs[0]['_num_']

    @classmethod
    async def find(cls, pk):  # 通过主键查找
        # find object by primary key
        rs = await select('%s where `%s`=?' % (cls.__select__, cls.__primary_key__), [pk], 1)
        if len(rs) == 0:
            return None
        return cls(**rs[0])

    # 实例方法：插入、更新、删除
    # 向数据库插入新数据
    async def save(self):
        # 构造非主键字段的数据列表，若某个字段数据为空则找默认值填充
        args = list(map(self.getValueOrDefault, self.__fields__))
        # 添加主键字段的数据
        args.append(self.getValueOrDefault(self.__primary_key__))
        # 传入默认__select__语句和参数，异步执行execute函数，返回影响的行数
        rows = await execute(self.__insert__, args)
        if rows != 1:
            logging.warning('failed to insert record: affected rows: %s' % rows)

    # 更新数据
    async def update(self):
        args = list(map(self.getValue, self.__fields__))
        args.append(self.getValue(self.__primary_key__))
        rows = await execute(self.__update__, args)
        if rows != 1:
            logging.warning('failed to update by primary key: affected rows: %s' % rows)

    # 删除数据
    async def remove(self):
        args = [self.getValue(self.__primary_key__)]
        rows = await execute(self.__delete__, args)
        if rows != 1:
            logging.warning('failed to remove by primary key: affected rows: %s' % rows)

# Field类和各种Field子类，负责保存数据库表的一条字段：字段名、字段类型、主键、默认值
# 各类型基类
class Field(object):

    def __init__(self, name, column_type, primary_key, default):
        self.name = name
        self.column_type = column_type
        self.primary_key = primary_key
        self.default = default

    def __str__(self):
        return '<%s, %s:%s>' % (self.__class__.__name__, self.column_type, self.name)

class StringField(Field):

    def __init__(self, name=None, primary_key=False, default=None, ddl='varchar(100)'):
        super().__init__(name, ddl, primary_key, default)

class BooleanField(Field):

    def __init__(self, name=None, default=False):
        super().__init__(name, 'boolean', False, default)

class IntegerField(Field):

    def __init__(self, name=None, primary_key=False, default=0):
        super().__init__(name, 'bigint', primary_key, default)

class FloatField(Field):

    def __init__(self, name=None, primary_key=False, default=0.0):
        super().__init__(name, 'real', primary_key, default)

class TextField(Field):

    def __init__(self, name=None, default=None):
        super().__init__(name, 'text', False, default)
