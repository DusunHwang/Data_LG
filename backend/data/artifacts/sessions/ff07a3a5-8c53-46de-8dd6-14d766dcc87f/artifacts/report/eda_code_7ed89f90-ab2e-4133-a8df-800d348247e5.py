import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
from sklearn.linear_model import LinearRegression
from sklearn.metrics import r2_score

# Load data
data = pd.read_parquet('data.parquet')

# MEDV와 상관이 높은 3개의 변수 선정 (예: RM, LSTAT, PTRATIO)
selected_columns = ['MEDV', 'RM', 'LSTAT', 'PTRATIO']

# 산점도 분석
for i, col in enumerate(selected_columns[1:]):
    plt.figure(figsize=(6, 4))
    sns.scatterplot(x=col, y='MEDV', data=data)
    plt.title(f'Scatter plot of MEDV vs {col}')
    plt.xlabel(col)
    plt.ylabel('MEDV')
    
    # R2 계산
    X = data[[col]]
    y = data['MEDV']
    model = LinearRegression()
    model.fit(X, y)
    y_pred = model.predict(X)
    r2 = r2_score(y, y_pred)
    
    plt.text(0.05, 0.95, f'R² = {r2:.2f}', transform=plt.gca().transAxes, fontsize=12, verticalalignment='top')
    plt.savefig(f'plot_{i+1}.png')
    plt.close()

# MEDV의 분포 분석
plt.figure(figsize=(6, 4))
sns.histplot(data['MEDV'], kde=True)
plt.title('Distribution of MEDV')
plt.xlabel('MEDV')
plt.ylabel('Frequency')
plt.savefig('plot_4.png')
plt.close()

# MEDV와 RM의 상관 분석
plt.figure(figsize=(6, 4))
sns.heatmap(data[['MEDV', 'RM']].corr(), annot=True, cmap='coolwarm', fmt=".2f")
plt.title('Correlation between MEDV and RM')
plt.savefig('plot_5.png')
plt.close()

# MEDV와 LSTAT의 상관 분석
plt.figure(figsize=(6, 4))
sns.heatmap(data[['MEDV', 'LSTAT']].corr(), annot=True, cmap='coolwarm', fmt=".2f")
plt.title('Correlation between MEDV and LSTAT')
plt.savefig('plot_6.png')
plt.close()

# MEDV와 PTRATIO의 상관 분석
plt.figure(figsize=(6, 4))
sns.heatmap(data[['MEDV', 'PTRATIO']].corr(), annot=True, cmap='coolwarm', fmt=".2f")
plt.title('Correlation between MEDV and PTRATIO')
plt.savefig('plot_7.png')
plt.close()