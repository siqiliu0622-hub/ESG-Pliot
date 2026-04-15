import streamlit as st
import streamlit.components.v1 as components
import os

# 设置网页标题
st.set_page_config(page_title="ESG 项目展示", layout="wide")

# 1. 尝试读取你的精美 HTML 文件
html_file_path = "ai_studio_code (5).html"

if os.path.exists(html_file_path):
    with open(html_file_path, 'r', encoding='utf-8') as f:
        html_content = f.read()
    
    # 2. 将你的 HTML 注入到网页中
    # 注意：因为你的原程序需要访问后端 API，
    # 这种方式最适合展示前端界面。
    st.write("### ESG 数字化管理平台")
    components.html(html_content, height=800, scrolling=True)
else:
    st.error(f"找不到前端文件：{html_file_path}，请确保它已经上传到 GitHub 根目录。")

st.info("提示：当前为预览模式。如果需要完整的数据库交互功能，建议部署为 Flask 应用。")
