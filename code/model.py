'''
Models for Multi-Task Temporal Shift Attention Networks for On-Device Contactless Vitals Measurement
Author: Xin Liu
further developed: Sarah Quehl
'''
from re import L, T
from numpy import float32
import tensorflow as tf
from tensorflow.python.keras import backend as K
from tensorflow.python.keras.layers import Conv2D, Conv3D, Input, AveragePooling2D, \
    multiply, Dense, Dropout, Flatten, AveragePooling3D
from tensorflow.python.keras.models import Model

class Attention_mask(tf.keras.layers.Layer):
    def call(self, x):
        xsum = K.sum(x, axis=1, keepdims=True)
        xsum = K.sum(xsum, axis=2, keepdims=True)
        xshape = K.int_shape(x)
        return x / xsum * xshape[1] * xshape[2] * 0.5

    def get_config(self):
        config = super(Attention_mask, self).get_config()
        return config

class TSM(tf.keras.layers.Layer):
    def call(self, x, n_frame, fold_div=3):
        nt, h, w, c = x.shape
        x = K.reshape(x, (-1, n_frame, h, w, c))
        fold = c // fold_div
        last_fold = c - (fold_div - 1) * fold
        out1, out2, out3 = tf.split(x, [fold, fold, last_fold], axis=-1)

        # Shift left
        padding_1 = tf.zeros_like(out1)
        padding_1 = padding_1[:, -1, :, :, :]
        padding_1 = tf.expand_dims(padding_1, 1)
        _, out1 = tf.split(out1, [1, n_frame - 1], axis=1)
        out1 = tf.concat([out1, padding_1], axis=1)

        # Shift right
        padding_2 = tf.zeros_like(out2)
        padding_2 = padding_2[:, 0, :, :, :]
        padding_2 = tf.expand_dims(padding_2, 1)
        out2, _ = tf.split(out2, [n_frame - 1, 1], axis=1)
        out2 = tf.concat([padding_2, out2], axis=1)

        out = tf.concat([out1, out2, out3], axis=-1)
        out = K.reshape(out, (-1, h, w, c))

        return out

    def get_config(self):
        config = super(TSM, self).get_config()
        return config


def TSM_Cov2D(x, n_frame, nb_filters=128, kernel_size=(3, 3), activation='tanh', padding='same'):
    x = TSM()(x, n_frame)
    x = Conv2D(nb_filters, kernel_size, padding=padding, activation=activation)(x)
    return x

# own layer:
class ownLayer_binaryPeak(tf.keras.layers.Layer):
    def call(self, x):
        out = self.get_peaks(x)

        return out

    def get_peaks(self, y):
        # y: (N,1)
        data_reshaped = tf.reshape(y, (1, -1, 1)) # (1, N, 1)
        max_pooled_in_tensor = tf.nn.max_pool(data_reshaped, (20,), 1,'SAME')
        maxima = tf.equal(data_reshaped, max_pooled_in_tensor) # (1, N, 1)
        maxima = tf.cast(maxima, tf.float32)
        maxima = tf.reshape(maxima, (-1,1))

        return maxima

    def get_config(self):
        config = super(ownLayer_binaryPeak, self).get_config()
        return config

class ownLayer_parameter(tf.keras.layers.Layer):
    def call(self, x, parameter):
        rr = self.get_rr(x)

        f_bpm = lambda: self.get_HR(tf.cast(rr, dtype=float32))
        f_sdnn = lambda: self.get_sdnn(tf.cast(rr, dtype=float32))
        f_pnn50 = lambda: self.get_pNN50(tf.cast(rr, dtype=float32))
        f_lfhf = lambda: self.get_lf_hf(tf.cast(rr, dtype=float32))

        result = []
        for item in parameter:
            result_part = tf.case([(tf.equal(item,'bpm'), f_bpm), (tf.equal(item,'sdnn'), f_sdnn), (tf.equal(item,'pnn50'), f_pnn50), (tf.equal(item,'lf_hf'), f_lfhf)], default=f_bpm)
            result.append(result_part)

        result = tf.convert_to_tensor(result)
        result = tf.reshape(result, (-1,1))
        return result
        
    def get_rr(self, y):
        # y: (N,1)
        fs = 50
        fs = tf.cond(tf.less(tf.shape(tf.reshape(y, (-1,))),tf.convert_to_tensor(1300)), lambda:  tf.cast(50, dtype=tf.int64), lambda:  tf.cast(40, dtype=tf.int64))

        indices = tf.where(tf.equal(tf.reshape(y, (-1,)),1))
        peak_locations = tf.squeeze(indices)
       
        def tf_diff_axis_0(a):
            return a[1:]-a[:-1]
        ibi_arr = tf_diff_axis_0(peak_locations)*fs

        mask = tf.logical_and(tf.greater_equal(ibi_arr,333),tf.less_equal(ibi_arr, 1500))
        mask.set_shape([None])
        
        rr_arr = tf.boolean_mask(ibi_arr, mask)

        return rr_arr
    
    def get_HR(self, rr):
        rr_mean = tf.reduce_mean(rr)
        HR = 60000/rr_mean
        return HR

    def get_sdnn(self, rr):
        return tf.math.reduce_std(rr) 

    def get_pNN50(self, rr):
        def tf_diff_axis_0(a):
            return a[1:]-a[:-1]
        rr_diff = tf_diff_axis_0(rr)

        size = tf.cast(tf.reduce_sum(tf.ones(tf.size(rr))), dtype=tf.float32)

        mask = (tf.greater(tf.abs(rr_diff),50))

        mask = tf.cast(mask, dtype=tf.int32)
        nn50 = tf.cast(tf.math.reduce_sum(mask), dtype=tf.float32)
        pNN50 = nn50/size
        return pNN50

    def get_lf_hf(self,rr):
        data = tf.reshape(rr, (-1,))
        f_true = lambda: data
        f_false = lambda: tf.convert_to_tensor([1], dtype=tf.float32)

        def custom_func(data):
            frq = tf.cast(tf.abs(tf.signal.rfft(data)), tf.float32)/tf.cast(tf.size(data),tf.float32)
            return tf.multiply(tf.pow(frq,2),tf.math.sqrt(tf.cast(2, tf.float32)))
        data = tf.case([(tf.greater(tf.size(rr), 2),f_true), (tf.less(tf.size(rr), 2),f_false)])

        frq = tf.keras.layers.Lambda(custom_func)(data)
        
        #frq = tf.cast(tf.abs(tf.signal.rfft(data)), tf.float32)/tf.cast(tf.size(data),tf.float32)
        dt = tf.math.reduce_mean(data) / 1000  # in sec
        t = tf.cast(tf.range(0, tf.size(frq)), tf.float32)
        t = tf.cast(t, tf.float32)/(tf.cast(dt, tf.float32)*tf.cast(tf.size(frq)*2, tf.float32))

        mask_lf = tf.cast(tf.logical_and(tf.greater_equal(t, 0.04), tf.less(t, 0.15)), tf.float32)
        lf = tf.maximum(tf.reduce_sum(frq*mask_lf), 0.000001)
        mask_hf = tf.cast(tf.logical_and(tf.greater_equal(t, 0.15), tf.less(t, 0.4)), tf.float32)
        hf = tf.maximum(tf.reduce_sum(frq*mask_hf), 0.000001)

        lf_hf = lf/hf

        return lf_hf

    def get_config(self):
        config = super(ownLayer_parameter, self).get_config()
        return config

# %%
# DEEPPHYS????
def CAN(nb_filters1, nb_filters2, input_shape, kernel_size=(3, 3), dropout_rate1=0.25, dropout_rate2=0.5,
        pool_size=(2, 2), nb_dense=128):
    diff_input = Input(shape=input_shape)
    rawf_input = Input(shape=input_shape)

    d1 = Conv2D(nb_filters1, kernel_size, padding='same', activation='tanh')(diff_input)
    d2 = Conv2D(nb_filters1, kernel_size, activation='tanh')(d1)

    r1 = Conv2D(nb_filters1, kernel_size, padding='same', activation='tanh')(rawf_input)
    r2 = Conv2D(nb_filters1, kernel_size, activation='tanh')(r1)

    g1 = Conv2D(1, (1, 1), padding='same', activation='sigmoid')(r2)
    g1 = Attention_mask()(g1)
    gated1 = multiply([d2, g1])

    d3 = AveragePooling2D(pool_size)(gated1)
    d4 = Dropout(dropout_rate1)(d3)

    r3 = AveragePooling2D(pool_size)(r2)
    r4 = Dropout(dropout_rate1)(r3)

    d5 = Conv2D(nb_filters2, kernel_size, padding='same', activation='tanh')(d4)
    d6 = Conv2D(nb_filters2, kernel_size, activation='tanh')(d5)

    r5 = Conv2D(nb_filters2, kernel_size, padding='same', activation='tanh')(r4)
    r6 = Conv2D(nb_filters2, kernel_size, activation='tanh')(r5)

    g2 = Conv2D(1, (1, 1), padding='same', activation='sigmoid')(r6)
    g2 = Attention_mask()(g2)
    gated2 = multiply([d6, g2])

    d7 = AveragePooling2D(pool_size)(gated2)
    d8 = Dropout(dropout_rate1)(d7)

    d9 = Flatten()(d8)
    d10 = Dense(nb_dense, activation='tanh')(d9)
    d11 = Dropout(dropout_rate2)(d10)
    out = Dense(1)(d11)
    model = Model(inputs=[diff_input, rawf_input], outputs=out)
    return model

# %% TS_CAN --> Paper
def TS_CAN(n_frame, nb_filters1, nb_filters2, input_shape, kernel_size=(3, 3), dropout_rate1=0.25, dropout_rate2=0.5,
           pool_size=(2, 2), nb_dense=128):
    diff_input = Input(shape=input_shape)
    rawf_input = Input(shape=input_shape)

    d1 = TSM_Cov2D(diff_input, n_frame, nb_filters1, kernel_size, padding='same', activation='tanh')
    d2 = TSM_Cov2D(d1, n_frame, nb_filters1, kernel_size, padding='valid', activation='tanh')

    r1 = Conv2D(nb_filters1, kernel_size, padding='same', activation='tanh')(rawf_input)
    r2 = Conv2D(nb_filters1, kernel_size, activation='tanh')(r1)

    g1 = Conv2D(1, (1, 1), padding='same', activation='sigmoid')(r2)
    g1 = Attention_mask()(g1)
    gated1 = multiply([d2, g1])

    d3 = AveragePooling2D(pool_size)(gated1)
    d4 = Dropout(dropout_rate1)(d3)

    r3 = AveragePooling2D(pool_size)(r2)
    r4 = Dropout(dropout_rate1)(r3)

    d5 = TSM_Cov2D(d4, n_frame, nb_filters2, kernel_size, padding='same', activation='tanh')
    d6 = TSM_Cov2D(d5, n_frame, nb_filters2, kernel_size, padding='valid', activation='tanh')

    r5 = Conv2D(nb_filters2, kernel_size, padding='same', activation='tanh')(r4)
    r6 = Conv2D(nb_filters2, kernel_size, activation='tanh')(r5)

    g2 = Conv2D(1, (1, 1), padding='same', activation='sigmoid')(r6)
    g2 = Attention_mask()(g2)
    gated2 = multiply([d6, g2])

    d7 = AveragePooling2D(pool_size)(gated2)
    d8 = Dropout(dropout_rate1)(d7)

    d9 = Flatten()(d8)
    d10 = Dense(nb_dense, activation='tanh')(d9)
    d11 = Dropout(dropout_rate2)(d10)
    out = Dense(1)(d11)
    model = Model(inputs=[diff_input, rawf_input], outputs=out)
    return model

#%% PTS_CAN --> Advanced TS_CAN with binary output signal
def PTS_CAN(n_frame, nb_filters1, nb_filters2, input_shape, kernel_size=(3, 3), dropout_rate1=0.25, dropout_rate2=0.5,
           pool_size=(2, 2), nb_dense=128):
    diff_input = Input(shape=input_shape)
    rawf_input = Input(shape=input_shape)

    d1 = TSM_Cov2D(diff_input, n_frame, nb_filters1, kernel_size, padding='same', activation='tanh')
    d2 = TSM_Cov2D(d1, n_frame, nb_filters1, kernel_size, padding='valid', activation='tanh')

    r1 = Conv2D(nb_filters1, kernel_size, padding='same', activation='tanh')(rawf_input)
    r2 = Conv2D(nb_filters1, kernel_size, activation='tanh')(r1)

    g1 = Conv2D(1, (1, 1), padding='same', activation='sigmoid')(r2)
    g1 = Attention_mask()(g1)
    gated1 = multiply([d2, g1])

    d3 = AveragePooling2D(pool_size)(gated1)
    d4 = Dropout(dropout_rate1)(d3)

    r3 = AveragePooling2D(pool_size)(r2)
    r4 = Dropout(dropout_rate1)(r3)

    d5 = TSM_Cov2D(d4, n_frame, nb_filters2, kernel_size, padding='same', activation='tanh')
    d6 = TSM_Cov2D(d5, n_frame, nb_filters2, kernel_size, padding='valid', activation='tanh')

    r5 = Conv2D(nb_filters2, kernel_size, padding='same', activation='tanh')(r4)
    r6 = Conv2D(nb_filters2, kernel_size, activation='tanh')(r5)

    g2 = Conv2D(1, (1, 1), padding='same', activation='sigmoid')(r6)
    g2 = Attention_mask()(g2)
    gated2 = multiply([d6, g2])

    d7 = AveragePooling2D(pool_size)(gated2)
    d8 = Dropout(dropout_rate1)(d7)

    d9 = Flatten()(d8)
    d10 = Dense(nb_dense, activation='tanh')(d9)
    d11 = Dropout(dropout_rate2)(d10)
    out1 = Dense(1, name='output_1')(d11)
    out_peaks = ownLayer_binaryPeak(name='output_2')(out1)

    model = Model(inputs=[diff_input, rawf_input], outputs=[out1, out_peaks])
    return model

# Advanced PTS-CAN: with additional parameter calculation
def PPTS_CAN(n_frame, nb_filters1, nb_filters2, input_shape, kernel_size=(3, 3), dropout_rate1=0.25, dropout_rate2=0.5,
           pool_size=(2, 2), nb_dense=128, parameter=None):
    diff_input = Input(shape=input_shape)
    rawf_input = Input(shape=input_shape)

    d1 = TSM_Cov2D(diff_input, n_frame, nb_filters1, kernel_size, padding='same', activation='tanh')
    d2 = TSM_Cov2D(d1, n_frame, nb_filters1, kernel_size, padding='valid', activation='tanh')

    r1 = Conv2D(nb_filters1, kernel_size, padding='same', activation='tanh')(rawf_input)
    r2 = Conv2D(nb_filters1, kernel_size, activation='tanh')(r1)

    g1 = Conv2D(1, (1, 1), padding='same', activation='sigmoid')(r2)
    g1 = Attention_mask()(g1)
    gated1 = multiply([d2, g1])

    d3 = AveragePooling2D(pool_size)(gated1)
    d4 = Dropout(dropout_rate1)(d3)

    r3 = AveragePooling2D(pool_size)(r2)
    r4 = Dropout(dropout_rate1)(r3)

    d5 = TSM_Cov2D(d4, n_frame, nb_filters2, kernel_size, padding='same', activation='tanh')
    d6 = TSM_Cov2D(d5, n_frame, nb_filters2, kernel_size, padding='valid', activation='tanh')

    r5 = Conv2D(nb_filters2, kernel_size, padding='same', activation='tanh')(r4)
    r6 = Conv2D(nb_filters2, kernel_size, activation='tanh')(r5)

    g2 = Conv2D(1, (1, 1), padding='same', activation='sigmoid')(r6)
    g2 = Attention_mask()(g2)
    gated2 = multiply([d6, g2])

    d7 = AveragePooling2D(pool_size)(gated2)
    d8 = Dropout(dropout_rate1)(d7)

    d9 = Flatten()(d8)
    d10 = Dense(nb_dense, activation='tanh')(d9)
    d11 = Dropout(dropout_rate2)(d10)
    out1 = Dense(1, name='output_1')(d11)
    out_peaks = ownLayer_binaryPeak(name='output_2')(out1)
    out_params = ownLayer_parameter(trainable=False, name='output_3')(out_peaks, parameter)
    #out_params = ownLayer_parameter(name='output_3')(out_peaks, parameter)

    model = Model(inputs=[diff_input, rawf_input], outputs=[out1, out_peaks, out_params])
    return model

# %%  --> PhysNet mit AttentionModule
def CAN_3D(n_frame, nb_filters1, nb_filters2, input_shape, kernel_size=(3, 3, 3), dropout_rate1=0.25, dropout_rate2=0.5,
           pool_size=(2, 2, 2), nb_dense=128):
    diff_input = Input(shape=input_shape)
    rawf_input = Input(shape=input_shape)

    d1 = Conv3D(nb_filters1, kernel_size, padding='same', activation='tanh')(diff_input)
    d2 = Conv3D(nb_filters1, kernel_size, activation='tanh')(d1)

    # Appearance Branch
    r1 = Conv3D(nb_filters1, kernel_size, padding='same', activation='tanh')(rawf_input)
    r2 = Conv3D(nb_filters1, kernel_size, activation='tanh')(r1)
    g1 = Conv3D(1, (1, 1, 1), padding='same', activation='sigmoid')(r2)
    g1 = Attention_mask()(g1)
    gated1 = multiply([d2, g1])
    d3 = AveragePooling3D(pool_size)(gated1)

    d4 = Dropout(dropout_rate1)(d3)
    d5 = Conv3D(nb_filters2, kernel_size, padding='same', activation='tanh')(d4)
    d6 = Conv3D(nb_filters2, kernel_size, activation='tanh')(d5)

    r3 = AveragePooling3D(pool_size)(r2)
    r4 = Dropout(dropout_rate1)(r3)
    r5 = Conv3D(nb_filters2, kernel_size, padding='same', activation='tanh')(r4)
    r6 = Conv3D(nb_filters2, kernel_size, activation='tanh')(r5)
    g2 = Conv3D(1, (1, 1, 1), padding='same', activation='sigmoid')(r6)
    g2 = Attention_mask()(g2)
    gated2 = multiply([d6, g2])

    d7 = AveragePooling3D(pool_size)(gated2)
    d8 = Dropout(dropout_rate1)(d7)
    d9 = Flatten()(d8)
    d10 = Dense(nb_dense, activation='tanh')(d9)
    d11 = Dropout(dropout_rate2)(d10)
    out = Dense(n_frame)(d11)
    model = Model(inputs=[diff_input, rawf_input], outputs=out)
    return model

# %%
def Hybrid_CAN(n_frame, nb_filters1, nb_filters2, input_shape_1, input_shape_2, kernel_size_1=(3, 3, 3),
               kernel_size_2=(3, 3), dropout_rate1=0.25, dropout_rate2=0.5, pool_size_1=(2, 2, 2), pool_size_2=(2, 2),
               nb_dense=128):
    diff_input = Input(shape=input_shape_1)
    rawf_input = Input(shape=input_shape_2)

    # Motion branch
    d1 = Conv3D(nb_filters1, kernel_size_1, padding='same', activation='tanh')(diff_input)
    d2 = Conv3D(nb_filters1, kernel_size_1, activation='tanh')(d1)

    # App branch
    r1 = Conv2D(nb_filters1, kernel_size_2, padding='same', activation='tanh')(rawf_input)
    r2 = Conv2D(nb_filters1, kernel_size_2, activation='tanh')(r1)

    # Mask from App (g1) * Motion Branch (d2)
    g1 = Conv2D(1, (1, 1), padding='same', activation='sigmoid')(r2)
    g1 = Attention_mask()(g1)
    g1 = K.expand_dims(g1, axis=-1)
    gated1 = multiply([d2, g1])

    # Motion Branch
    d3 = AveragePooling3D(pool_size_1)(gated1)
    d4 = Dropout(dropout_rate1)(d3)
    d5 = Conv3D(nb_filters2, kernel_size_1, padding='same', activation='tanh')(d4)
    d6 = Conv3D(nb_filters2, kernel_size_1, activation='tanh')(d5)

    # App branch
    r3 = AveragePooling2D(pool_size_2)(r2)
    r4 = Dropout(dropout_rate1)(r3)
    r5 = Conv2D(nb_filters2, kernel_size_2, padding='same', activation='tanh')(r4)
    r6 = Conv2D(nb_filters2, kernel_size_2, activation='tanh')(r5)

    # Mask from App (g2) * Motion Branch (d6)
    g2 = Conv2D(1, (1, 1), padding='same', activation='sigmoid')(r6)
    g2 = Attention_mask()(g2)
    g2 = K.repeat_elements(g2, d6.shape[3], axis=-1)
    g2 = K.expand_dims(g2, axis=-1)
    gated2 = multiply([d6, g2])

    # Motion Branch
    d7 = AveragePooling3D(pool_size_1)(gated2)
    d8 = Dropout(dropout_rate1)(d7)

    # Motion Branch
    d9 = Flatten()(d8)
    d10 = Dense(nb_dense, activation='tanh')(d9)
    d11 = Dropout(dropout_rate2)(d10)
    out = Dense(n_frame)(d11)

    model = Model(inputs=[diff_input, rawf_input], outputs=out)
    return model

####### Multi Task Models (Blood Volume Pulse and Respiration Rate)
# %% MT_CAN
def MT_CAN(nb_filters1, nb_filters2, input_shape, kernel_size=(3, 3), dropout_rate1=0.25, dropout_rate2=0.5,
           pool_size=(2, 2), nb_dense=128):
    diff_input = Input(shape=input_shape)
    rawf_input = Input(shape=input_shape)

    d1 = Conv2D(nb_filters1, kernel_size, padding='same', activation='tanh')(diff_input)
    d2 = Conv2D(nb_filters1, kernel_size, activation='tanh')(d1)

    r1 = Conv2D(nb_filters1, kernel_size, padding='same', activation='tanh')(rawf_input)
    r2 = Conv2D(nb_filters1, kernel_size, activation='tanh')(r1)

    g1 = Conv2D(1, (1, 1), padding='same', activation='sigmoid')(r2)
    g1 = Attention_mask()(g1)
    gated1 = multiply([d2, g1])

    d3 = AveragePooling2D(pool_size)(gated1)
    d4 = Dropout(dropout_rate1)(d3)

    r3 = AveragePooling2D(pool_size)(r2)
    r4 = Dropout(dropout_rate1)(r3)

    d5 = Conv2D(nb_filters2, kernel_size, padding='same', activation='tanh')(d4)
    d6 = Conv2D(nb_filters2, kernel_size, activation='tanh')(d5)

    r5 = Conv2D(nb_filters2, kernel_size, padding='same', activation='tanh')(r4)
    r6 = Conv2D(nb_filters2, kernel_size, activation='tanh')(r5)

    g2 = Conv2D(1, (1, 1), padding='same', activation='sigmoid')(r6)
    g2 = Attention_mask()(g2)
    gated2 = multiply([d6, g2])

    d7 = AveragePooling2D(pool_size)(gated2)
    d8 = Dropout(dropout_rate1)(d7)

    d9 = Flatten()(d8)
    d10_y = Dense(nb_dense, activation='tanh')(d9)
    d11_y = Dropout(dropout_rate2)(d10_y)
    out_y = Dense(1, name='output_1')(d11_y)

    d10_r = Dense(nb_dense, activation='tanh')(d9)
    d11_r = Dropout(dropout_rate2)(d10_r)
    out_r = Dense(1, name='output_2')(d11_r)

    model = Model(inputs=[diff_input, rawf_input], outputs=[out_y, out_r])
    return model

# %% MTTS-CAN
def MTTS_CAN(n_frame, nb_filters1, nb_filters2, input_shape, kernel_size=(3, 3), dropout_rate1=0.25,
             dropout_rate2=0.5, pool_size=(2, 2), nb_dense=128):
    diff_input = Input(shape=input_shape)
    rawf_input = Input(shape=input_shape)

    d1 = TSM_Cov2D(diff_input, n_frame, nb_filters1, kernel_size, padding='same', activation='tanh')
    d2 = TSM_Cov2D(d1, n_frame, nb_filters1, kernel_size, padding='valid', activation='tanh')

    r1 = Conv2D(nb_filters1, kernel_size, padding='same', activation='tanh')(rawf_input)
    r2 = Conv2D(nb_filters1, kernel_size, activation='tanh')(r1)

    g1 = Conv2D(1, (1, 1), padding='same', activation='sigmoid')(r2)
    g1 = Attention_mask()(g1)
    gated1 = multiply([d2, g1])

    d3 = AveragePooling2D(pool_size)(gated1)
    d4 = Dropout(dropout_rate1)(d3)

    r3 = AveragePooling2D(pool_size)(r2)
    r4 = Dropout(dropout_rate1)(r3)

    d5 = TSM_Cov2D(d4, n_frame, nb_filters2, kernel_size, padding='same', activation='tanh')
    d6 = TSM_Cov2D(d5, n_frame, nb_filters2, kernel_size, padding='valid', activation='tanh')

    r5 = Conv2D(nb_filters2, kernel_size, padding='same', activation='tanh')(r4)
    r6 = Conv2D(nb_filters2, kernel_size, activation='tanh')(r5)

    g2 = Conv2D(1, (1, 1), padding='same', activation='sigmoid')(r6)
    g2 = Attention_mask()(g2)
    gated2 = multiply([d6, g2])

    d7 = AveragePooling2D(pool_size)(gated2)
    d8 = Dropout(dropout_rate1)(d7)

    d9 = Flatten()(d8)

    d10_y = Dense(nb_dense, activation='tanh')(d9)
    d11_y = Dropout(dropout_rate2)(d10_y)
    out_y = Dense(1, name='output_1')(d11_y)

    d10_r = Dense(nb_dense, activation='tanh')(d9)
    d11_r = Dropout(dropout_rate2)(d10_r)
    out_r = Dense(1, name='output_2')(d11_r)

    model = Model(inputs=[diff_input, rawf_input], outputs=[out_y, out_r])
    return model

# %%
def MT_CAN_3D(n_frame, nb_filters1, nb_filters2, input_shape, kernel_size=(3, 3, 3), dropout_rate1=0.25,
              dropout_rate2=0.5, pool_size=(2, 2, 2), nb_dense=128):
    diff_input = Input(shape=input_shape)
    rawf_input = Input(shape=input_shape)

    d1 = Conv3D(nb_filters1, kernel_size, padding='same', activation='tanh')(diff_input)
    d2 = Conv3D(nb_filters1, kernel_size, activation='tanh')(d1)

    # Appearance Branch
    r1 = Conv3D(nb_filters1, kernel_size, padding='same', activation='tanh')(rawf_input)
    r2 = Conv3D(nb_filters1, kernel_size, activation='tanh')(r1)
    g1 = Conv3D(1, (1, 1, 1), padding='same', activation='sigmoid')(r2)
    g1 = Attention_mask()(g1)
    gated1 = multiply([d2, g1])

    d3 = AveragePooling3D(pool_size)(gated1)
    d4 = Dropout(dropout_rate1)(d3)
    d5 = Conv3D(nb_filters2, kernel_size, padding='same', activation='tanh')(d4)
    d6 = Conv3D(nb_filters2, kernel_size, activation='tanh')(d5)

    r3 = AveragePooling3D(pool_size)(r2)
    r4 = Dropout(dropout_rate1)(r3)
    r5 = Conv3D(nb_filters2, kernel_size, padding='same', activation='tanh')(r4)
    r6 = Conv3D(nb_filters2, kernel_size, activation='tanh')(r5)
    g2 = Conv3D(1, (1, 1, 1), padding='same', activation='sigmoid')(r6)
    g2 = Attention_mask()(g2)
    gated2 = multiply([d6, g2])
    d7 = AveragePooling3D(pool_size)(gated2)
    d8 = Dropout(dropout_rate1)(d7)

    d9 = Flatten()(d8)
    d10_y = Dense(nb_dense, activation='tanh')(d9)
    d11_y = Dropout(dropout_rate2)(d10_y)
    out_y = Dense(n_frame, name='output_1')(d11_y)

    d10_r = Dense(nb_dense, activation='tanh')(d9)
    d11_r = Dropout(dropout_rate2)(d10_r)
    out_r = Dense(n_frame, name='output_2')(d11_r)

    model = Model(inputs=[diff_input, rawf_input], outputs=[out_y, out_r])

    return model

# %%
def MT_Hybrid_CAN(n_frame, nb_filters1, nb_filters2, input_shape_1, input_shape_2, kernel_size_1=(3, 3, 3),
                  kernel_size_2=(3, 3), dropout_rate1=0.25, dropout_rate2=0.5, pool_size_1=(2, 2, 2),
                  pool_size_2=(2, 2), nb_dense=128):
    diff_input = Input(shape=input_shape_1)
    rawf_input = Input(shape=input_shape_2)

    # Motion branch
    d1 = Conv3D(nb_filters1, kernel_size_1, padding='same', activation='tanh')(diff_input)
    d2 = Conv3D(nb_filters1, kernel_size_1, activation='tanh')(d1)

    # App branch
    r1 = Conv2D(nb_filters1, kernel_size_2, padding='same', activation='tanh')(rawf_input)
    r2 = Conv2D(nb_filters1, kernel_size_2, activation='tanh')(r1)

    # Mask from App (g1) * Motion Branch (d2)
    g1 = Conv2D(1, (1, 1), padding='same', activation='sigmoid')(r2)
    g1 = Attention_mask()(g1)
    g1 = K.expand_dims(g1, axis=-1)
    gated1 = multiply([d2, g1])

    # Motion Branch
    d3 = AveragePooling3D(pool_size_1)(gated1)
    d4 = Dropout(dropout_rate1)(d3)
    d5 = Conv3D(nb_filters2, kernel_size_1, padding='same', activation='tanh')(d4)
    d6 = Conv3D(nb_filters2, kernel_size_1, activation='tanh')(d5)

    # App branch
    r3 = AveragePooling2D(pool_size_2)(r2)
    r4 = Dropout(dropout_rate1)(r3)
    r5 = Conv2D(nb_filters2, kernel_size_2, padding='same', activation='tanh')(r4)
    r6 = Conv2D(nb_filters2, kernel_size_2, activation='tanh')(r5)

    # Mask from App (g2) * Motion Branch (d6)
    g2 = Conv2D(1, (1, 1), padding='same', activation='sigmoid')(r6)
    g2 = Attention_mask()(g2)
    g2 = K.repeat_elements(g2, d6.shape[3], axis=-1)
    g2 = K.expand_dims(g2, axis=-1)
    gated2 = multiply([d6, g2])

    # Motion Branch
    d7 = AveragePooling3D(pool_size_1)(gated2)
    d8 = Dropout(dropout_rate1)(d7)

    # Motion Branch
    d9 = Flatten()(d8)

    d10_y = Dense(nb_dense, activation='tanh')(d9)
    d11_y = Dropout(dropout_rate2)(d10_y)
    out_y = Dense(n_frame, name='output_1')(d11_y)

    d10_r = Dense(nb_dense, activation='tanh')(d9)
    d11_r = Dropout(dropout_rate2)(d10_r)
    out_r = Dense(n_frame, name='output_2')(d11_r)

    model = Model(inputs=[diff_input, rawf_input], outputs=[out_y, out_r])
    return model

# %%
class HeartBeat(tf.keras.callbacks.Callback):
    def __init__(self, train_gen, test_gen, args, cv_split, save_dir):
        super(HeartBeat, self).__init__()
        self.train_gen = train_gen
        self.test_gen = test_gen
        self.args = args
        self.cv_split = cv_split
        self.save_dir = save_dir

    def on_epoch_end(self, epoch, logs={}):
        print('PROGRESS: 0.00%')
