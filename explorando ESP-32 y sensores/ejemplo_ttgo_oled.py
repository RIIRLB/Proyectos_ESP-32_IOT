HARDWARE = 'TIPO_2'

def funcion_tipo_1(l1 = ''):
    print(f'Soy Tipo 1: \t {l1}')
    
def funcion_tipo_2(l1 = ''):
    print(f'Soy Tipo 2: \t {l1}')

if HARDWARE == 'TIPO_1':
    funcion = funcion_tipo_1
else:
    funcion = funcion_tipo_2
    
funcion('Probando la idea ...')	