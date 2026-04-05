import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
from sklearn.linear_model import LinearRegression
from sklearn.metrics import r2_score

# Load data
data = pd.read_parquet('data.parquet')

# MperR와 상관계수가 높은 3개 컬럼 분석
correlation = data[['MperR', 'RM', 'LSTAT', 'PTRATIO']].corr()
correlation_matrix = correlation['MperR'].sort_values(ascending=False)
top_3_columns = correlation_matrix.index[1:4]

# MperR vs RM 산점도
plt.figure(figsize=(8, 6))
sns.scatterplot(x='RM', y='MperR', data=data)
model = LinearRegression()
model.fit(data[['RM']], data['MperR'])
y_pred = model.predict(data[['RM']])
r2 = r2_score(data['MperR'], y_pred)
plt.title(f'MperR vs RM (R² = {r2:.2f})')
plt.xlabel('RM')
plt.ylabel('MperR')
plt.savefig('plot_1.png')
plt.close()

# MperR vs LSTAT 산점도
plt.figure(figsize=(8, 6))
sns.scatterplot(x='LSTAT', y='MperR', data=data)
model = LinearRegression()
model.fit(data[['LSTAT']], data['MperR'])
y_pred = model.predict(data[['LSTAT']])
r2 = r2_score(data['MperR'], y_pred)
plt.title(f'MperR vs LSTAT (R² = {r2:.2f})')
plt.xlabel('LSTAT')
plt.ylabel('MperR')
plt.savefig('plot_2.png')
plt.close()

# MperR vs PTRATIO 산점도
plt.figure(figsize=(8, 6))
sns.scatterplot(x='PTRATIO', y='MperR', data=data)
model = LinearRegression()
model.fit(data[['PTRATIO']], data['MperR'])
y_pred = model.predict(data[['PTRATIO']])
r2 = r2_score(data['MperR'], y_pred)
plt.title(f'MperR vs PTRATIO (R² = {r2:.2f})')
plt.xlabel('PTRATIO')
plt.ylabel('MperR')
plt.savefig('plot_3.png')
plt.close()

# MperR와 상관계수가 높은 컬럼의 분포 분석
plt.figure(figsize=(15, 5))
for i, col in enumerate(['RM', 'LSTAT', 'PTRATIO']):
    plt.subplot(1, 3, i+1)
    sns.histplot(data[col], kde=True)
    plt.title(f'Distribution of {col}')
plt.tight_layout()
plt.savefig('plot_4.png')
plt.close()