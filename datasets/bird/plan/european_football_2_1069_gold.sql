{
  "relOp": "LogicalProject",
  "project": [
    {
      "kind": "DIVIDE",
      "operator": "/",
      "type": "REAL",
      "operands": [
        {
          "kind": "CAST",
          "operator": "CAST",
          "type": "REAL",
          "operands": [
            {
              "kind": "INPUT_REF",
              "index": 0,
              "name": "$0",
              "type": "INTEGER"
            }
          ]
        },
        {
          "kind": "CASE",
          "operator": "CASE",
          "type": "BIGINT",
          "operands": [
            {
              "kind": "EQUALS",
              "operator": "=",
              "type": "BOOLEAN",
              "operands": [
                {
                  "kind": "INPUT_REF",
                  "index": 1,
                  "name": "$1",
                  "type": "BIGINT"
                },
                {
                  "kind": "LITERAL",
                  "value": 0,
                  "type": "BIGINT",
                  "nullable": false,
                  "precision": 19
                }
              ]
            },
            {
              "kind": "LITERAL",
              "value": "NULL",
              "type": "BIGINT",
              "nullable": true,
              "precision": 19
            },
            {
              "kind": "CAST",
              "operator": "CAST",
              "type": "BIGINT",
              "operands": [
                {
                  "kind": "INPUT_REF",
                  "index": 1,
                  "name": "$1",
                  "type": "BIGINT"
                }
              ]
            }
          ]
        }
      ]
    }
  ],
  "id": "6",
  "inputs": [
    {
      "relOp": "LogicalAggregate",
      "keys": [],
      "aggs": [
        {
          "operator": "SUM",
          "distinct": false,
          "ignoreNulls": false,
          "operands": [
            {
              "column": 0,
              "type": "INTEGER"
            }
          ],
          "type": "INTEGER",
          "name": null
        },
        {
          "operator": "COUNT",
          "distinct": false,
          "ignoreNulls": false,
          "operands": [],
          "type": "BIGINT",
          "name": null
        }
      ],
      "id": "5",
      "inputs": [
        {
          "relOp": "LogicalProject",
          "project": [
            {
              "kind": "INPUT_REF",
              "index": 11,
              "name": "$11",
              "type": "INTEGER"
            },
            {
              "kind": "INPUT_REF",
              "index": 7,
              "name": "$7",
              "type": "INTEGER"
            }
          ],
          "id": "4",
          "inputs": [
            {
              "relOp": "LogicalFilter",
              "condition": {
                "kind": "AND",
                "operator": "AND",
                "type": "BOOLEAN",
                "operands": [
                  {
                    "kind": "GREATER_THAN",
                    "operator": ">",
                    "type": "BOOLEAN",
                    "operands": [
                      {
                        "kind": "INPUT_REF",
                        "index": 5,
                        "name": "$5",
                        "type": "INTEGER"
                      },
                      {
                        "kind": "LITERAL",
                        "value": 170,
                        "type": "INTEGER",
                        "nullable": false,
                        "precision": 10
                      }
                    ]
                  },
                  {
                    "kind": "GREATER_THAN_OR_EQUAL",
                    "operator": ">=",
                    "type": "BOOLEAN",
                    "operands": [
                      {
                        "kind": "OTHER_FUNCTION",
                        "operator": "SUBSTR",
                        "type": "VARCHAR",
                        "operands": [
                          {
                            "kind": "INPUT_REF",
                            "index": 10,
                            "name": "$10",
                            "type": "VARCHAR"
                          },
                          {
                            "kind": "LITERAL",
                            "value": 1,
                            "type": "INTEGER",
                            "nullable": false,
                            "precision": 10
                          },
                          {
                            "kind": "LITERAL",
                            "value": 4,
                            "type": "INTEGER",
                            "nullable": false,
                            "precision": 10
                          }
                        ]
                      },
                      {
                        "kind": "LITERAL",
                        "value": "2010",
                        "type": "CHAR",
                        "nullable": false,
                        "precision": 4
                      }
                    ]
                  },
                  {
                    "kind": "LESS_THAN_OR_EQUAL",
                    "operator": "<=",
                    "type": "BOOLEAN",
                    "operands": [
                      {
                        "kind": "OTHER_FUNCTION",
                        "operator": "SUBSTR",
                        "type": "VARCHAR",
                        "operands": [
                          {
                            "kind": "INPUT_REF",
                            "index": 10,
                            "name": "$10",
                            "type": "VARCHAR"
                          },
                          {
                            "kind": "LITERAL",
                            "value": 1,
                            "type": "INTEGER",
                            "nullable": false,
                            "precision": 10
                          },
                          {
                            "kind": "LITERAL",
                            "value": 4,
                            "type": "INTEGER",
                            "nullable": false,
                            "precision": 10
                          }
                        ]
                      },
                      {
                        "kind": "LITERAL",
                        "value": "2015",
                        "type": "CHAR",
                        "nullable": false,
                        "precision": 4
                      }
                    ]
                  }
                ]
              },
              "variableset": "[]",
              "id": "3",
              "inputs": [
                {
                  "relOp": "LogicalJoin",
                  "joinType": "inner",
                  "condition": {
                    "kind": "EQUALS",
                    "operator": "=",
                    "type": "BOOLEAN",
                    "operands": [
                      {
                        "kind": "INPUT_REF",
                        "index": 1,
                        "name": "$1",
                        "type": "INTEGER"
                      },
                      {
                        "kind": "INPUT_REF",
                        "index": 9,
                        "name": "$9",
                        "type": "INTEGER"
                      }
                    ]
                  },
                  "id": "2",
                  "inputs": [
                    {
                      "relOp": "LogicalTableScan",
                      "table": "Player",
                      "id": "0",
                      "inputs": []
                    },
                    {
                      "relOp": "LogicalTableScan",
                      "table": "Player_Attributes",
                      "id": "1",
                      "inputs": []
                    }
                  ]
                }
              ]
            }
          ]
        }
      ]
    }
  ]
}