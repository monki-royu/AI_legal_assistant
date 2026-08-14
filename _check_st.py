import streamlit as st
print(f"Streamlit {st.__version__}")
print(f"st.html: {hasattr(st, chr(104)+chr(116)+chr(109)+chr(108))}")
print(f"st.query_params: {hasattr(st, chr(113)+chr(117)+chr(101)+chr(114)+chr(121)+chr(95)+chr(112)+chr(97)+chr(114)+chr(97)+chr(109)+chr(115))}")
