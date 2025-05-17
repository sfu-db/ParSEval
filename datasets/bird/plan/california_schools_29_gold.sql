{
  "relOp": "LogicalProject",
  "project": [
    {
      "kind": "INPUT_REF",
      "index": 35,
      "name": "$35",
      "type": "VARCHAR"
    },
    {
      "kind": "INPUT_REF",
      "index": 54,
      "name": "$54",
      "type": "VARCHAR"
    }
  ],
  "id": "10",
  "inputs": [
    {
      "relOp": "LogicalFilter",
      "condition": {
        "kind": "AND",
        "operator": "AND",
        "type": "BOOLEAN",
        "operands": [
          {
            "kind": "EQUALS",
            "operator": "=",
            "type": "BOOLEAN",
            "operands": [
              {
                "kind": "INPUT_REF",
                "index": 53,
                "name": "$53",
                "type": "VARCHAR"
              },
              {
                "kind": "LITERAL",
                "value": "Locally funded",
                "type": "VARCHAR",
                "nullable": false,
                "precision": -1
              }
            ]
          },
          {
            "kind": "GREATER_THAN",
            "operator": ">",
            "type": "BOOLEAN",
            "operands": [
              {
                "kind": "MINUS",
                "operator": "-",
                "type": "FLOAT",
                "operands": [
                  {
                    "kind": "INPUT_REF",
                    "index": 18,
                    "name": "$18",
                    "type": "FLOAT"
                  },
                  {
                    "kind": "INPUT_REF",
                    "index": 23,
                    "name": "$23",
                    "type": "FLOAT"
                  }
                ]
              },
              {
                "kind": "SCALAR_QUERY",
                "operator": "$SCALAR_QUERY",
                "operands": [],
                "query": [
                  {
                    "relOp": "LogicalAggregate",
                    "keys": [],
                    "aggs": [
                      {
                        "operator": "AVG",
                        "distinct": false,
                        "ignoreNulls": false,
                        "operands": [
                          {
                            "column": 0,
                            "type": "FLOAT"
                          }
                        ],
                        "type": "FLOAT",
                        "name": "EXPR$0"
                      }
                    ],
                    "id": "5",
                    "inputs": [
                      {
                        "relOp": "LogicalProject",
                        "project": [
                          {
                            "kind": "MINUS",
                            "operator": "-",
                            "type": "FLOAT",
                            "operands": [
                              {
                                "kind": "INPUT_REF",
                                "index": 18,
                                "name": "$18",
                                "type": "FLOAT"
                              },
                              {
                                "kind": "INPUT_REF",
                                "index": 23,
                                "name": "$23",
                                "type": "FLOAT"
                              }
                            ]
                          }
                        ],
                        "id": "4",
                        "inputs": [
                          {
                            "relOp": "LogicalFilter",
                            "condition": {
                              "kind": "EQUALS",
                              "operator": "=",
                              "type": "BOOLEAN",
                              "operands": [
                                {
                                  "kind": "INPUT_REF",
                                  "index": 53,
                                  "name": "$53",
                                  "type": "VARCHAR"
                                },
                                {
                                  "kind": "LITERAL",
                                  "value": "Locally funded",
                                  "type": "VARCHAR",
                                  "nullable": false,
                                  "precision": -1
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
                                      "index": 0,
                                      "name": "$0",
                                      "type": "VARCHAR"
                                    },
                                    {
                                      "kind": "INPUT_REF",
                                      "index": 29,
                                      "name": "$29",
                                      "type": "VARCHAR"
                                    }
                                  ]
                                },
                                "id": "2",
                                "inputs": [
                                  {
                                    "relOp": "LogicalTableScan",
                                    "table": "frpm",
                                    "id": "0",
                                    "inputs": []
                                  },
                                  {
                                    "relOp": "LogicalTableScan",
                                    "table": "schools",
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
            ]
          }
        ]
      },
      "variableset": "[]",
      "id": "9",
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
                "index": 0,
                "name": "$0",
                "type": "VARCHAR"
              },
              {
                "kind": "INPUT_REF",
                "index": 29,
                "name": "$29",
                "type": "VARCHAR"
              }
            ]
          },
          "id": "8",
          "inputs": [
            {
              "relOp": "LogicalTableScan",
              "table": "frpm",
              "id": "6",
              "inputs": []
            },
            {
              "relOp": "LogicalTableScan",
              "table": "schools",
              "id": "7",
              "inputs": []
            }
          ]
        }
      ]
    }
  ]
}