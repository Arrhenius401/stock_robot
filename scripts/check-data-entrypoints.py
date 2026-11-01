"""静态检查数据取数入口，防止新增未声明绕行。"""
from core.collection_contract import main

if __name__ == "__main__":
    raise SystemExit(main())
